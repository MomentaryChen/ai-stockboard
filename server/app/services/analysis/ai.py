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

Gate 1 has its own entrance, `get_cached`, which stops there and returns None
instead of falling through to gates 2 and 3. `get_or_create` is the door that
may spend money and so must stay a POST; `get_cached` is the door a page can
open on render.

**Depth is part of the subject, not a rendering flag.** A quick verdict is drawn
from the price series; a deep one additionally reads `chip_day` and
`fundamentals_annual`. They are two different answers about the same trading
day, so `depth` joins the cache key and both rows coexist -- pressing one button
must not evict what the other was paid for. Every entrance above takes it:
a free read asking for one depth must never be served the other's verdict.
Both spend from the one allowance, because the bill being defended belongs to
the deployment and does not care which prompt produced the request.
"""

from __future__ import annotations

import datetime
import logging
import zoneinfo

from pydantic import ValidationError
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
from app.services import valuation as valuation_service
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
        # The exchange's own PE, preferred over one derived from last completed
        # year's EPS -- see `hold_features.fundamentals_features`. `latest` is
        # cache-only, which is the constraint this whole function is under.
        valuation=valuation_service.latest(db, sid),
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


def _stored_deep(row: AiAnalysis) -> DeepInputs | None:
    """The deep inputs this row was generated from, out of its own audit copy.

    The free readers below serve whatever has already been paid for, and a deep
    verdict rendered without them would carry the quick lane's disclaimer --
    which states the opposite of what was read. Re-deriving them would be two
    more queries per sid, which on a twenty-row board is forty; the row already
    carries the copy, so this costs nothing.

    Tolerant of a row written before the deep lane existed, or by a future
    shape: an unreadable audit copy degrades to "no deep block", not to a 500
    on a page that was only trying to show a cached answer.
    """
    if row.depth != "deep":
        return None
    try:
        return DeepInputs.model_validate((row.features or {}).get("deep"))
    except ValidationError:
        logger.warning("ai row %s has an unreadable deep audit copy", row.id)
        return None


#: Every wording currently in service. A row keyed under a superseded prompt is
#: not served for free: it would be presented beside today's measurements as if
#: it had been drawn from them.
def _live_prompt_versions() -> tuple[str, str]:
    return (prompts.PROMPT_VERSION, deep_prompts.PROMPT_VERSION)


def _best(rows: list[AiAnalysis]) -> AiAnalysis | None:
    """The better-informed of the verdicts stored for one subject.

    Depth is an input where it costs money and an output where it does not:
    `get_or_create` is told which depth to pay for, while the free readers
    serve whichever has already been bought. Refusing to show a deep verdict
    because the caller did not ask for one would be withholding the better
    answer for no reason -- nobody is charged either way.
    """
    if not rows:
        return None
    return next((r for r in rows if r.depth == "deep"), rows[0])


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


def get_cached_many(
    db: Session,
    *,
    names: dict[str, str],
    rows_by_sid: dict[str, list[DailyPrice]],
    locale: str = DEFAULT_LOCALE,
) -> list[AiAnalysisResponse]:
    """`get_cached` over a basket, in one lookup instead of one per sid.

    Sids with nothing stored are dropped rather than reported: on a watchlist
    that is the ordinary state of most rows, and the caller's job is to show
    what has been paid for, not to enumerate what has not.

    The `as_of` each sid is keyed under differs -- a stock that did not trade
    on the latest session ends on an earlier bar -- so the row filter cannot be
    pushed entirely into SQL. It is one indexed read over the basket plus a
    dictionary match, which is still a single round trip.
    """
    locale = normalise_locale(locale)

    as_of_by_sid: dict[str, PriceFeatures] = {}
    for sid, rows in rows_by_sid.items():
        extracted = feature_service.extract(rows)
        if extracted is not None:
            as_of_by_sid[sid] = extracted
    if not as_of_by_sid:
        return []

    model = model_settings.active_model(db)
    stored = db.execute(
        select(AiAnalysis).where(
            AiAnalysis.sid.in_(list(as_of_by_sid)),
            AiAnalysis.as_of.in_({f.as_of for f in as_of_by_sid.values()}),
            AiAnalysis.model == model,
            AiAnalysis.prompt_version.in_(_live_prompt_versions()),
            AiAnalysis.locale == locale,
        )
    ).scalars()

    # A sid can now have two rows for one trading day -- one per depth -- so the
    # basket is grouped before it is rendered and the better one wins. Doing it
    # in SQL would need a window function for one row per group; the basket is
    # capped at twenty, so it is cheaper to sort it here.
    by_sid: dict[str, list[AiAnalysis]] = {}
    for row in stored:
        extracted = as_of_by_sid.get(row.sid)
        # The `as_of` IN clause is a union across the basket, so a row can come
        # back matching *another* sid's trading day. This is the exact match.
        if extracted is None or row.as_of != extracted.as_of:
            continue
        by_sid.setdefault(row.sid, []).append(row)

    out: list[AiAnalysisResponse] = []
    for sid, candidates in by_sid.items():
        row = _best(candidates)
        if row is None:
            continue
        stock = traditional.build_stock(rows_by_sid[sid])
        out.append(
            _response(
                sid=sid,
                name=names.get(sid, sid),
                row=row,
                features=as_of_by_sid[sid],
                deep=_stored_deep(row),
                traditional_result=traditional.best_four_point(stock),
                cached=True,
            )
        )
    return out


def get_cached(
    db: Session,
    *,
    sid: str,
    name: str,
    rows: list[DailyPrice],
    locale: str = DEFAULT_LOCALE,
    depth: AiDepth | None = None,
) -> AiAnalysisResponse | None:
    """The stored verdict for these bars, or None. Never generates, never charges.

    `get_or_create` is the metered door and has to be a POST; this is the free
    one. Separating them is what lets a page *display* a verdict it did not pay
    for: before this existed the only way to discover a cached row was to POST,
    so every reader was offered a button and a board that had already been
    judged looked unjudged until somebody clicked.

    `depth` is the difference between "show me whatever has been paid for" and
    "show me *this* answer". Omitted, the better-informed row wins (`_best`),
    which is what a board wants. Named, the lookup is pinned to that depth and
    a miss is a miss -- a reader who asked for the quick call must never be
    handed the deep one wearing the quick one's label, because the two disagree
    about what the model was allowed to see and a panel comparing them would be
    comparing one answer with itself.

    Returns None for every kind of absence -- too few bars, no row for this
    trading day, a model or prompt the row predates. The caller cannot act on
    the distinction: all of them mean "nothing free to show, offer the button",
    and raising InsufficientData here would make a 20-sid board read as broken
    because two of its stocks are newly listed.
    """
    locale = normalise_locale(locale)

    extracted = feature_service.extract(rows)
    if extracted is None:
        return None

    model = model_settings.active_model(db)

    if depth is not None:
        row = _find(db, sid, extracted.as_of, locale, model, depth)
    else:
        # Both depths, best first -- see `_best`.
        stored = list(
            db.execute(
                select(AiAnalysis).where(
                    AiAnalysis.sid == sid,
                    AiAnalysis.as_of == extracted.as_of,
                    AiAnalysis.model == model,
                    AiAnalysis.prompt_version.in_(_live_prompt_versions()),
                    AiAnalysis.locale == locale,
                )
            ).scalars()
        )
        row = _best(stored)
    if row is None:
        return None

    # After the lookup, not before: on a board where most sids miss, this is
    # the only work in the function worth skipping.
    traditional_result = traditional.best_four_point(traditional.build_stock(rows))
    return _response(
        sid=sid,
        name=name,
        row=row,
        features=extracted,
        deep=_stored_deep(row),
        traditional_result=traditional_result,
        cached=True,
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
