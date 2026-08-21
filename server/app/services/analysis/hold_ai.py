"""When to spend a Gemini request on a 存股 verdict, and when not to.

The same three gates as `ai.py` -- shared cache, per-account daily quota,
process-wide rate limiter -- against a different table. Read that module for
why each one is there; only what differs is written down here.

**The quota is shared with the technical lane, not doubled.** One Gemini budget
per account per day, counted across both `ai_analysis` and `ai_hold_analysis`.
The alternative was tempting and wrong: two quotas would mean shipping this
feature silently doubles what every existing account can spend, and the cost
being defended belongs to the deployment's card, which does not care which
prompt produced the request.

**The cache key is the trading day, again.** Debatable here in a way it is not
next door: a company's suitability for a ten-year hold does not change between
Tuesday and Wednesday, so a longer-lived cache would be defensible. It is keyed
on the day anyway because the price is in the features -- yield and PE both
move with the close -- and a verdict that cited a 5.4% yield would go on being
served after the price rose enough to make it 4.1%.

**Depth is part of the subject, not a rendering flag** -- the rule `ai.py`
learned first. A quick verdict is drawn from the checklist's snapshot; a deep
one additionally reads the year-by-year earnings and payout record, the
valuation band out of `valuation_day`, and `chip_day`. They are two different
answers about the same company on the same trading day, so `depth` joins the
cache key and both rows coexist -- pressing one button must not evict what the
other was paid for, and a reader who asked for one must never be handed the
other. Both spend from the one allowance, because the bill being defended
belongs to the deployment and does not care which prompt produced the request.

**A verdict does not require full coverage.** `chen_rules` reports what it
could not check, the prompt is told to lower its confidence accordingly, and
the model answers on what exists. The only hard floor is a trading day to key
the row on: no bars, no `as_of`, nothing to cache.

**The free read.** `get_cached` stops at gate 1 and returns None instead of
falling through to the meter, exactly as its technical sibling does. It is what
lets a page show a 存股 verdict somebody already paid for -- and, with `depth`
named, what lets a reader move between the two assessments without either one
costing anything or borrowing the other's answer.
"""

from __future__ import annotations

import datetime
import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AiHoldAnalysis,
    AppUser,
    DailyPrice,
    DividendEvent,
    FundamentalsAnnual,
)
from app.schemas import (
    AiDepth,
    AiHoldAnalysisResponse,
    AiHoldVerdict,
    ChenRuleResult,
    HoldDeepInputs,
    HoldFeatures,
)
from app.services import chip as chip_service
from app.services import valuation as valuation_service
from app.services.analysis import ai as ai_service
from app.services.analysis import (
    hold_deep_features,
    hold_deep_prompts,
    hold_gemini,
    hold_prompts,
    model_settings,
)

logger = logging.getLogger(__name__)

#: Same set the technical lane supports, and for the same reason: a locale the
#: prompt has no wording for is served the default rather than asking Gemini to
#: write in a language nobody reviewed.
SUPPORTED_LOCALES = ai_service.SUPPORTED_LOCALES
DEFAULT_LOCALE = ai_service.DEFAULT_LOCALE


class InsufficientData(RuntimeError):
    """No usable bars, so there is no trading day to key a verdict on."""


def normalise_locale(locale: str | None) -> str:
    return ai_service.normalise_locale(locale)


def prompt_version(depth: AiDepth) -> str:
    """Which wording keys the cache for this depth.

    The two lanes version independently: editing the deep rules must not
    invalidate every quick verdict in the table, and vice versa.
    """
    return (
        hold_deep_prompts.PROMPT_VERSION
        if depth == "deep"
        else hold_prompts.PROMPT_VERSION
    )


#: Every wording currently in service. A row keyed under a superseded prompt is
#: not served for free: it would be presented beside today's measurements as if
#: it had been drawn from them.
def _live_prompt_versions() -> tuple[str, str]:
    return (hold_prompts.PROMPT_VERSION, hold_deep_prompts.PROMPT_VERSION)


def _row_to_verdict(row: AiHoldAnalysis) -> AiHoldVerdict:
    return AiHoldVerdict(
        suitability=row.suitability,
        confidence=row.confidence,
        headline=row.headline,
        reasons=list(row.reasons or []),
        risks=list(row.risks or []),
        agrees_with_rules=row.agrees_with_rules,
    )


def _stored_deep(row: AiHoldAnalysis) -> HoldDeepInputs | None:
    """The deep inputs this row was generated from, out of its own audit copy.

    Same trade as `ai._stored_deep`: the free reader serves whatever has been
    paid for, and a deep verdict rendered without its inputs would carry the
    quick lane's disclaimer, which states the opposite of what was read.

    Tolerant of a row written before this lane existed, or by a future shape:
    an unreadable audit copy degrades to "no deep block", not to a 500 on a page
    that was only trying to show a cached answer.
    """
    if row.depth != "deep":
        return None
    try:
        return HoldDeepInputs.model_validate((row.features or {}).get("deep"))
    except ValidationError:
        logger.warning("hold row %s has an unreadable deep audit copy", row.id)
        return None


def _best(rows: list[AiHoldAnalysis]) -> AiHoldAnalysis | None:
    """The better-informed of the verdicts stored for one subject.

    Depth is an input where it costs money and an output where it does not --
    `get_or_create` is told which depth to pay for, while an unpinned free read
    serves whichever has already been bought. Refusing to show a deep verdict
    because the caller did not ask for one would be withholding the better
    answer for no reason: nobody is charged either way.
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
) -> AiHoldAnalysis | None:
    return db.execute(
        select(AiHoldAnalysis).where(
            AiHoldAnalysis.sid == sid,
            AiHoldAnalysis.as_of == as_of,
            AiHoldAnalysis.model == model,
            AiHoldAnalysis.prompt_version == prompt_version(depth),
            AiHoldAnalysis.locale == locale,
            AiHoldAnalysis.depth == depth,
        )
    ).scalar_one_or_none()


def _response(
    *,
    row: AiHoldAnalysis,
    features: HoldFeatures,
    rules: ChenRuleResult,
    deep: HoldDeepInputs | None,
    cached: bool,
) -> AiHoldAnalysisResponse:
    return AiHoldAnalysisResponse(
        sid=features.sid,
        name=features.name,
        as_of=row.as_of,
        generated_at=row.created_at,
        model=row.model,
        prompt_version=row.prompt_version,
        locale=row.locale,
        depth=row.depth,
        cached=cached,
        verdict=_row_to_verdict(row),
        # The freshly computed snapshot, not the stored one -- same choice as
        # ai.py. The card shows what is true now; the stored copy stays as the
        # audit trail of what the verdict was drawn from.
        features=features,
        deep=deep,
        rules=rules,
    )


def deep_inputs(
    db: Session,
    *,
    features: HoldFeatures,
    prices: list[DailyPrice],
    dividends: list[DividendEvent],
    fundamentals: list[FundamentalsAnnual],
) -> HoldDeepInputs:
    """The extra blocks a deep hold verdict reads, fetched from nothing.

    Cache-only, and this is the function where that is decided -- the same
    constraint `ai._deep_inputs` is under, for a slightly different reason. The
    button is on a stock page rather than a board here, but the snapshot behind
    it is the widest single-stock read in the service (two years of bars, eleven
    of dividend reports), and adding two fetching reads to it would make one
    visitor to a cold stock queue tens of exchange calls on the limiter the
    realtime poll shares.

    What fills the stores instead: `chip_refresh` and the valuation job, both
    of which cover the whole market from board-wide reports. A miss here means
    a job has not run, not that this stock is uncovered, and the prompt is told
    about it either way.
    """
    assert features.as_of is not None  # callers check; the row cannot key without it
    return hold_deep_features.extract(
        as_of=features.as_of,
        prices=prices,
        chips=chip_service.read_recent(db, features.sid, hold_deep_features.CHIP_WINDOW),
        fundamentals=fundamentals,
        dividends=dividends,
        valuations=valuation_service.read_recent(
            db, features.sid, hold_deep_features.VALUATION_WINDOW
        ),
    )


def get_cached(
    db: Session,
    *,
    features: HoldFeatures,
    rules: ChenRuleResult,
    locale: str = DEFAULT_LOCALE,
    depth: AiDepth | None = None,
    deep: HoldDeepInputs | None = None,
) -> AiHoldAnalysisResponse | None:
    """The stored 存股 verdict for this snapshot, or None. Never charges.

    `get_or_create` is the metered door and has to be a POST; this is the free
    one, and it exists for the reason its technical sibling does: before it, the
    only way to discover a stored verdict was to POST, so a company that had
    already been assessed rendered as though it never had.

    `depth` pins which of the two answers counts as a hit. Omitted, the better
    informed one wins. Named, a miss stays a miss -- a reader looking at the
    深度 lane must not be shown the checklist-only verdict wearing its label.

    `deep` is the freshly derived block for a deep hit; without it the row's own
    audit copy is rehydrated instead, which is the cheaper path when the caller
    has not built one.
    """
    locale = normalise_locale(locale)
    if features.as_of is None:
        return None

    model = model_settings.active_model(db)

    if depth is not None:
        row = _find(db, features.sid, features.as_of, locale, model, depth)
    else:
        stored = list(
            db.execute(
                select(AiHoldAnalysis).where(
                    AiHoldAnalysis.sid == features.sid,
                    AiHoldAnalysis.as_of == features.as_of,
                    AiHoldAnalysis.model == model,
                    AiHoldAnalysis.prompt_version.in_(_live_prompt_versions()),
                    AiHoldAnalysis.locale == locale,
                )
            ).scalars()
        )
        row = _best(stored)

    if row is None:
        return None
    return _response(
        row=row,
        features=features,
        rules=rules,
        deep=deep if deep is not None else _stored_deep(row),
        cached=True,
    )


def get_or_create(
    db: Session,
    *,
    features: HoldFeatures,
    rules: ChenRuleResult,
    user: AppUser,
    prices: list[DailyPrice] | None = None,
    dividends: list[DividendEvent] | None = None,
    fundamentals: list[FundamentalsAnnual] | None = None,
    locale: str = DEFAULT_LOCALE,
    force: bool = False,
    depth: AiDepth = "quick",
) -> AiHoldAnalysisResponse:
    """The stored 存股 verdict for this snapshot, generating one only if absent.

    Takes the features and the checklist rather than raw rows: the caller has
    already built both for the free `/analysis/chen` response, and rebuilding
    them here would risk the model being asked about numbers the card never
    showed. The raw rows come too, but only because the deep lane needs the
    bars, the payout events and the annual figures to derive its extra blocks
    from -- and re-reading those here would be three more queries for rows the
    caller is already holding.

    `depth` selects how much the model is shown, and is part of the cache key
    rather than a rendering flag: a quick and a deep verdict for the same
    company on the same day are two different answers, and pressing one button
    must never evict the other's result.
    """
    locale = normalise_locale(locale)

    if features.as_of is None:
        raise InsufficientData(
            f"No daily bars stored for {features.sid}; open the stock page first"
        )
    as_of = features.as_of

    # Resolved once per call, exactly as ai.py does it, so the cache lookup, the
    # provider request and the stored row all name the same engine. The admin
    # can change the active model between requests; re-reading it later in this
    # function would let that produce a row keyed under a model that never ran.
    model = model_settings.active_model(db)

    # A deep call without the rows would derive an empty record and report it as
    # a company with no history -- a wrong answer that looks exactly like a
    # right one about an uncovered stock. The rows are optional in the signature
    # because the quick lane genuinely does not need them; asking for one depth
    # without its inputs is a caller bug, not a coverage gap.
    assert depth != "deep" or prices is not None, "deep hold verdict needs the rows"

    # Derived before the cache lookup rather than only on a miss: a hit returns
    # these to the panel too, so the reader of someone else's verdict still sees
    # the record and the coverage gaps it was drawn from.
    deep = (
        deep_inputs(
            db,
            features=features,
            prices=prices or [],
            dividends=dividends or [],
            fundamentals=fundamentals or [],
        )
        if depth == "deep"
        else None
    )

    if not force:
        existing = _find(db, features.sid, as_of, locale, model, depth)
        if existing is not None:
            return _response(
                row=existing, features=features, rules=rules, deep=deep, cached=True
            )

    status = ai_service.quota_status(db, user)
    if status.used >= status.limit:
        raise ai_service.QuotaExceeded(status)

    if depth == "deep":
        assert deep is not None  # set above whenever depth is "deep"
        generation = hold_gemini.generate_deep(
            features=features, deep=deep, rules=rules, locale=locale, model=model
        )
    else:
        generation = hold_gemini.generate(
            features=features, rules=rules, locale=locale, model=model
        )

    row = AiHoldAnalysis(
        sid=features.sid,
        as_of=as_of,
        model=model,
        prompt_version=prompt_version(depth),
        locale=locale,
        depth=depth,
        suitability=generation.verdict.suitability,
        confidence=generation.verdict.confidence,
        headline=generation.verdict.headline[:500],
        reasons=generation.verdict.reasons,
        risks=generation.verdict.risks,
        agrees_with_rules=generation.verdict.agrees_with_rules,
        rule_score=rules.score,
        rule_suitability=rules.suitability,
        # The audit trail of this verdict's input. For a deep call that has to
        # include the record and the band, or the stored row would claim the
        # model saw only the checklist's aggregates.
        features=(
            features.model_dump(mode="json")
            if deep is None
            else {
                "snapshot": features.model_dump(mode="json"),
                "deep": deep.model_dump(mode="json"),
            }
        ),
        input_tokens=generation.input_tokens,
        output_tokens=generation.output_tokens,
        latency_ms=generation.latency_ms,
        requested_by=user.id,
    )

    if force:
        # A regeneration replaces the row it supersedes -- same reasoning, and
        # the same accepted consequence, as ai.py: the quota counts surviving
        # rows, so an ADMIN regenerating their own verdict is not charged. That
        # is tolerable only because `force` is ADMIN-only.
        #
        # Every column of the unique key is matched, `depth` included. Scoping
        # this to one depth is not a detail: regenerating a deep verdict must
        # supersede the deep row and leave the quick one alone.
        db.execute(
            AiHoldAnalysis.__table__.delete().where(
                AiHoldAnalysis.sid == features.sid,
                AiHoldAnalysis.as_of == as_of,
                AiHoldAnalysis.model == model,
                AiHoldAnalysis.prompt_version == prompt_version(depth),
                AiHoldAnalysis.locale == locale,
                AiHoldAnalysis.depth == depth,
            )
        )

    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Two callers missed the cache for the same subject and both paid. The
        # loser keeps the winner's row, so every reader of this day sees one
        # verdict.
        db.rollback()
        existing = _find(db, features.sid, as_of, locale, model, depth)
        if existing is None:
            raise
        logger.info(
            "hold verdict raced for sid=%s as_of=%s depth=%s",
            features.sid, as_of, depth,
        )
        return _response(
            row=existing, features=features, rules=rules, deep=deep, cached=True
        )

    db.refresh(row)
    return _response(
        row=row, features=features, rules=rules, deep=deep, cached=False
    )
