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
   charged for reading someone else's answer.
3. **The process-wide rate limiter** in the provider adapter, which is the last
   line and protects the deployment's quota rather than any one account's.

`force` re-generates past gate 1. It is ADMIN-only at the router, for the same
reason `force` on the history routes is: it is the one knob that turns a cached
endpoint back into a metered one.

Gate 1 has its own entrance, `get_cached`, which stops there and returns None
instead of falling through to gates 2 and 3. `get_or_create` is the door that
may spend money and so must stay a POST; `get_cached` is the door a page can
open on render.
"""

from __future__ import annotations

import datetime
import logging
import zoneinfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiAnalysis, AppUser, DailyPrice
from app.schemas import (
    AiAnalysisResponse,
    AiQuotaStatus,
    AiVerdict,
    BestFourPointResult,
    PriceFeatures,
)
from app.services.analysis import features as feature_service
from app.services.analysis import gemini, model_settings, prompts, traditional

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
    start, end = _day_bounds()
    used = db.execute(
        select(func.count())
        .select_from(AiAnalysis)
        .where(AiAnalysis.requested_by == user.id, AiAnalysis.created_at >= start)
    ).scalar_one()
    return AiQuotaStatus(used=used, limit=daily_quota(user), resets_at=end)


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
    db: Session, sid: str, as_of: datetime.date, locale: str, model: str
) -> AiAnalysis | None:
    return db.execute(
        select(AiAnalysis).where(
            AiAnalysis.sid == sid,
            AiAnalysis.as_of == as_of,
            AiAnalysis.model == model,
            AiAnalysis.prompt_version == prompts.PROMPT_VERSION,
            AiAnalysis.locale == locale,
        )
    ).scalar_one_or_none()


def _response(
    *,
    sid: str,
    name: str,
    row: AiAnalysis,
    features: PriceFeatures,
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
        cached=cached,
        verdict=_row_to_verdict(row),
        features=features,
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
            AiAnalysis.prompt_version == prompts.PROMPT_VERSION,
            AiAnalysis.locale == locale,
        )
    ).scalars()

    out: list[AiAnalysisResponse] = []
    for row in stored:
        extracted = as_of_by_sid.get(row.sid)
        # The `as_of` IN clause is a union across the basket, so a row can come
        # back matching *another* sid's trading day. This is the exact match.
        if extracted is None or row.as_of != extracted.as_of:
            continue
        stock = traditional.build_stock(rows_by_sid[row.sid])
        out.append(
            _response(
                sid=row.sid,
                name=names.get(row.sid, row.sid),
                row=row,
                features=extracted,
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
) -> AiAnalysisResponse | None:
    """The stored verdict for these bars, or None. Never generates, never charges.

    `get_or_create` is the metered door and has to be a POST; this is the free
    one. Separating them is what lets a page *display* a verdict it did not pay
    for: before this existed the only way to discover a cached row was to POST,
    so every reader was offered a button and a board that had already been
    judged looked unjudged until somebody clicked.

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

    row = _find(db, sid, extracted.as_of, locale, model_settings.active_model(db))
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
) -> AiAnalysisResponse:
    """The stored verdict for these bars, generating one only if there is none."""
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

    if not force:
        existing = _find(db, sid, extracted.as_of, locale, model)
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
                traditional_result=traditional_result,
                cached=True,
            )

    status = quota_status(db, user)
    if status.used >= status.limit:
        raise QuotaExceeded(status)

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
        prompt_version=prompts.PROMPT_VERSION,
        locale=locale,
        action=generation.verdict.action,
        size=generation.verdict.size,
        confidence=generation.verdict.confidence,
        headline=generation.verdict.headline[:500],
        reasons=generation.verdict.reasons,
        risks=generation.verdict.risks,
        features=extracted.model_dump(mode="json"),
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
        db.execute(
            AiAnalysis.__table__.delete().where(
                AiAnalysis.sid == sid,
                AiAnalysis.as_of == extracted.as_of,
                AiAnalysis.model == model,
                AiAnalysis.prompt_version == prompts.PROMPT_VERSION,
                AiAnalysis.locale == locale,
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
        existing = _find(db, sid, extracted.as_of, locale, model)
        if existing is None:
            raise
        logger.info("ai verdict raced for sid=%s as_of=%s", sid, extracted.as_of)
        return _response(
            sid=sid,
            name=name,
            row=existing,
            features=extracted,
            traditional_result=traditional_result,
            cached=True,
        )

    db.refresh(row)
    return _response(
        sid=sid,
        name=name,
        row=row,
        features=extracted,
        traditional_result=traditional_result,
        cached=False,
    )
