"""AI-assisted analysis: when to spend a provider request, and when not to.

The generation itself is one call into the active provider adapter (Gemini
today). Almost everything here is about *not* making that call, because this is
the first feature in the service whose upstream costs money per request and is
triggered by a button rather than by a schedule. Prompt wording is shared in
`prompts.py`; adapters only transport it.

Three gates, in order:

1. **The shared cache.** A verdict is about a trading day, not about a moment, so
   (sid, as_of, model, prompt_version, locale) identifies it completely and the
   second reader of a session's verdict pays nothing. This is why the button can
   sit on every watchlist card without the cost scaling with the board.
2. **The per-account daily quota**, counted from rows this account actually paid
   for. Cache hits are free and are not counted -- otherwise a user would be
   charged for reading someone else's answer. One allowance covers every AI
   lane: `quota_status` counts `ai_hold_analysis` alongside this table, because
   the budget belongs to the deployment's bill rather than to a prompt.
3. **The process-wide rate limiter** in the provider adapter, which is the last
   line and protects the deployment's quota rather than any one account's.

`force` re-generates past gate 1. It is ADMIN-only at the router, for the same
reason `force` on the history routes is: it is the one knob that turns a cached
endpoint back into a metered one.

**Depth is part of the subject, not a rendering flag.** A quick verdict is drawn
from the price series; a deep one additionally reads `chip_day` and
`fundamentals_annual`. They are two different answers about the same trading
day, so `depth` joins the cache key and both rows coexist -- pressing one button
must not evict what the other was paid for. Both spend from the one allowance
above, because the bill being defended belongs to the deployment and does not
care which prompt produced the request.
"""

from __future__ import annotations

import datetime
import logging
import zoneinfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiAnalysis, AiHoldAnalysis, AppUser, DailyPrice
from app.schemas import (
    AiAnalysisResponse,
    AiDepth,
    AiQuotaStatus,
    AiVerdict,
    BestFourPointResult,
    DeepInputs,
    PriceFeatures,
)
from app.services import chip as chip_service
from app.services import fundamentals as fundamentals_service
from app.services.analysis import deep_features, deep_prompts
from app.services.analysis import features as feature_service
from app.services.analysis import gemini, hold_features, model_settings, prompts, traditional

logger = logging.getLogger(__name__)

settings = get_settings()

#: Locales the prompt has wording for. Anything else is served the default
#: rather than silently asking the model to write in a language nobody reviewed.
SUPPORTED_LOCALES = ("zh-TW", "en")
DEFAULT_LOCALE = "zh-TW"


class InsufficientData(RuntimeError):
    """Fewer stored bars than the feature set needs to mean anything."""


class QuotaExceeded(RuntimeError):
    def __init__(self, status: AiQuotaStatus):
        self.status = status
        super().__init__(f"Daily AI quota reached ({status.used}/{status.limit})")


def normalise_locale(locale: str | None) -> str:
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


def _day_bounds() -> tuple[datetime.datetime, datetime.datetime]:
    """Start of today and start of tomorrow, in the scheduler's wall clock.

    The quota resets on the exchange's calendar day, not on UTC's: a user in
    Taipei who spends their allowance on Friday evening expects it back on
    Saturday morning, and 08:00 Taipei is still Friday in UTC.
    """
    tz = zoneinfo.ZoneInfo(settings.scheduler_timezone)
    now = datetime.datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=1)


def daily_quota(user: AppUser) -> int:
    return (
        settings.ai_admin_daily_quota
        if user.role == "ADMIN"
        else settings.ai_daily_quota
    )


def quota_status(db: Session, user: AppUser) -> AiQuotaStatus:
    """One allowance across every AI lane, not one per lane.

    Rows are counted from both `ai_analysis` and `ai_hold_analysis` because the
    budget being defended is the deployment's Gemini bill, which does not care
    which prompt produced the request. Counting them separately would mean that
    adding a second lane silently doubled what every existing account could
    spend, without anyone deciding to raise the limit.

    Cache hits are still free in both: only rows an account actually paid for
    carry its id.
    """
    start, end = _day_bounds()
    used = 0
    for table in (AiAnalysis, AiHoldAnalysis):
        used += db.execute(
            select(func.count())
            .select_from(table)
            .where(table.requested_by == user.id, table.created_at >= start)
        ).scalar_one()
    return AiQuotaStatus(used=used, limit=daily_quota(user), resets_at=end)


def prompt_version(depth: AiDepth) -> str:
    """Which wording keys the cache for this depth.

    The two lanes version independently: editing the deep rules must not
    invalidate every quick verdict in the table, and vice versa.
    """
    return deep_prompts.PROMPT_VERSION if depth == "deep" else prompts.PROMPT_VERSION


def _deep_inputs(
    db: Session, sid: str, features: PriceFeatures, rows: list[DailyPrice]
) -> DeepInputs:
    """Chip and annual figures for this stock, read and never fetched.

    Cache-only on purpose, and this is the function where that is decided. The
    deep button sits on watchlist rows as well as the stock page, so a fetching
    read here would let one board turn into tens of exchange calls queued on
    the limiter the realtime poll shares. `chip_refresh` warms the market
    nightly, so the common case is covered anyway; an uncovered stock becomes a
    coverage gap the prompt is told about, which is the honest answer rather
    than a stalled request.
    """
    return deep_features.extract(
        as_of=features.as_of,
        latest_close=features.latest_close,
        prices=rows,
        chips=chip_service.read_recent(db, sid, deep_features.CHIP_WINDOW),
        fundamentals=fundamentals_service.read(db, sid, hold_features.WINDOW_YEARS),
    )


def _row_to_verdict(row: AiAnalysis) -> AiVerdict:
    return AiVerdict(
        action=row.action,
        size=row.size,
        confidence=row.confidence,
        headline=row.headline,
        reasons=list(row.reasons or []),
        risks=list(row.risks or []),
    )


def _find(
    db: Session,
    sid: str,
    as_of: datetime.date,
    locale: str,
    model: str,
    depth: AiDepth,
) -> AiAnalysis | None:
    return db.execute(
        select(AiAnalysis).where(
            AiAnalysis.sid == sid,
            AiAnalysis.as_of == as_of,
            AiAnalysis.model == model,
            AiAnalysis.prompt_version == prompt_version(depth),
            AiAnalysis.locale == locale,
            AiAnalysis.depth == depth,
        )
    ).scalar_one_or_none()


def _response(
    *,
    sid: str,
    name: str,
    row: AiAnalysis,
    features: PriceFeatures,
    deep: DeepInputs | None,
    traditional_result: BestFourPointResult,
    cached: bool,
) -> AiAnalysisResponse:
    return AiAnalysisResponse(
        sid=sid,
        name=name,
        as_of=row.as_of,
        generated_at=row.created_at,
        model=row.model,
        prompt_version=row.prompt_version,
        locale=row.locale,
        depth=row.depth,
        cached=cached,
        verdict=_row_to_verdict(row),
        features=features,
        # Freshly derived, like `features` and for the same reason: the panel
        # shows what is true now, while the row's stored copy stays as the
        # audit trail of what the verdict was actually drawn from.
        deep=deep,
        traditional=traditional_result,
    )


def get_or_create(
    db: Session,
    *,
    sid: str,
    name: str,
    rows: list[DailyPrice],
    user: AppUser,
    locale: str = DEFAULT_LOCALE,
    force: bool = False,
    depth: AiDepth = "quick",
) -> AiAnalysisResponse:
    """The stored verdict for these bars, generating one only if there is none.

    `depth` selects how much the model is shown, and is part of the cache key
    rather than a rendering flag: a quick and a deep verdict for the same stock
    on the same day are two different answers and both are worth keeping, so
    pressing one button must never evict the other's result.
    """
    locale = normalise_locale(locale)

    extracted = feature_service.extract(rows)
    if extracted is None:
        raise InsufficientData(
            f"Need at least {feature_service.MIN_SAMPLES_FOR_AI} trading days"
        )

    stock = traditional.build_stock(rows)
    traditional_result = traditional.best_four_point(stock)

    # Resolved once per call so the cache lookup, the provider request and the
    # stored row all name the same engine. Re-reading the setting later in the
    # function would let an admin flip mid-request and produce a row keyed
    # under a model that never ran.
    model = model_settings.active_model(db)

    # Read before the cache lookup rather than only on a miss: a hit returns
    # these to the panel too, so the reader of someone else's verdict still
    # sees the flow and the coverage gaps it was drawn from.
    deep = _deep_inputs(db, sid, extracted, rows) if depth == "deep" else None

    if not force:
        existing = _find(db, sid, extracted.as_of, locale, model, depth)
        if existing is not None:
            return _response(
                sid=sid,
                name=name,
                row=existing,
                # Deliberately the freshly computed features, not the stored
                # ones: the card shows what is true now, while the stored copy
                # stays as the audit trail of what the verdict was drawn from.
                # They differ only if features.py changed since.
                features=extracted,
                deep=deep,
                traditional_result=traditional_result,
                cached=True,
            )

    status = quota_status(db, user)
    if status.used >= status.limit:
        raise QuotaExceeded(status)

    if depth == "deep":
        assert deep is not None  # set above whenever depth is "deep"
        generation = gemini.generate_deep(
            sid=sid,
            name=name,
            features=extracted,
            deep=deep,
            traditional=traditional_result,
            locale=locale,
            model=model,
        )
    else:
        generation = gemini.generate(
            sid=sid,
            name=name,
            features=extracted,
            traditional=traditional_result,
            locale=locale,
            model=model,
        )

    row = AiAnalysis(
        sid=sid,
        as_of=extracted.as_of,
        model=model,
        prompt_version=prompt_version(depth),
        locale=locale,
        depth=depth,
        action=generation.verdict.action,
        size=generation.verdict.size,
        confidence=generation.verdict.confidence,
        headline=generation.verdict.headline[:500],
        reasons=generation.verdict.reasons,
        risks=generation.verdict.risks,
        # The audit trail of this verdict's input. For a deep call that has to
        # include the chip and annual blocks, or the stored row would claim the
        # model saw only a price series.
        features=(
            extracted.model_dump(mode="json")
            if deep is None
            else {
                "price": extracted.model_dump(mode="json"),
                "deep": deep.model_dump(mode="json"),
            }
        ),
        input_tokens=generation.input_tokens,
        output_tokens=generation.output_tokens,
        latency_ms=generation.latency_ms,
        requested_by=user.id,
    )

    if force:
        # A regeneration replaces the row it supersedes: the unique key is the
        # point of the table, and keeping both would make "the verdict for this
        # day" ambiguous for every reader after it.
        #
        # Accepted consequence: the quota is counted from surviving rows, so an
        # account regenerating its own verdict deletes the row it is charged for
        # and the call comes out free. Tolerated because `force` is ADMIN-only
        # -- charging it correctly needs an append-only ledger separate from the
        # cache, which is a lot of table for an operator escape hatch.
        #
        # Every column of the unique key is matched, `depth` included. Scoping
        # this to one depth is not a detail: regenerating a deep verdict must
        # supersede the deep row and leave the quick one alone, and a delete
        # that named only the quick prompt version would do exactly the
        # opposite of what was asked.
        db.execute(
            AiAnalysis.__table__.delete().where(
                AiAnalysis.sid == sid,
                AiAnalysis.as_of == extracted.as_of,
                AiAnalysis.model == model,
                AiAnalysis.prompt_version == prompt_version(depth),
                AiAnalysis.locale == locale,
                AiAnalysis.depth == depth,
            )
        )

    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two callers missed the cache for the same subject and both paid. The
        # loser keeps the winner's row rather than its own, so every reader of
        # this trading day sees one verdict.
        db.rollback()
        existing = _find(db, sid, extracted.as_of, locale, model, depth)
        if existing is None:
            raise
        logger.info(
            "ai verdict raced for sid=%s as_of=%s depth=%s",
            sid, extracted.as_of, depth,
        )
        return _response(
            sid=sid,
            name=name,
            row=existing,
            features=extracted,
            deep=deep,
            traditional_result=traditional_result,
            cached=True,
        )

    db.refresh(row)
    return _response(
        sid=sid,
        name=name,
        row=row,
        features=extracted,
        deep=deep,
        traditional_result=traditional_result,
        cached=False,
    )
