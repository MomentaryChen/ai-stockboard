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

**A verdict does not require full coverage.** `chen_rules` reports what it
could not check, the prompt is told to lower its confidence accordingly, and
the model answers on what exists. The only hard floor is a trading day to key
the row on: no bars, no `as_of`, nothing to cache.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AiHoldAnalysis, AppUser
from app.schemas import (
    AiHoldAnalysisResponse,
    AiHoldVerdict,
    ChenRuleResult,
    HoldFeatures,
)
from app.services.analysis import ai as ai_service
from app.services.analysis import hold_gemini, hold_prompts, model_settings

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


def _row_to_verdict(row: AiHoldAnalysis) -> AiHoldVerdict:
    return AiHoldVerdict(
        suitability=row.suitability,
        confidence=row.confidence,
        headline=row.headline,
        reasons=list(row.reasons or []),
        risks=list(row.risks or []),
        agrees_with_rules=row.agrees_with_rules,
    )


def _find(
    db: Session, sid: str, as_of: datetime.date, locale: str, model: str
) -> AiHoldAnalysis | None:
    from sqlalchemy import select

    return db.execute(
        select(AiHoldAnalysis).where(
            AiHoldAnalysis.sid == sid,
            AiHoldAnalysis.as_of == as_of,
            AiHoldAnalysis.model == model,
            AiHoldAnalysis.prompt_version == hold_prompts.PROMPT_VERSION,
            AiHoldAnalysis.locale == locale,
        )
    ).scalar_one_or_none()


def _response(
    *,
    row: AiHoldAnalysis,
    features: HoldFeatures,
    rules: ChenRuleResult,
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
        cached=cached,
        verdict=_row_to_verdict(row),
        # The freshly computed snapshot, not the stored one -- same choice as
        # ai.py. The card shows what is true now; the stored copy stays as the
        # audit trail of what the verdict was drawn from.
        features=features,
        rules=rules,
    )


def get_or_create(
    db: Session,
    *,
    features: HoldFeatures,
    rules: ChenRuleResult,
    user: AppUser,
    locale: str = DEFAULT_LOCALE,
    force: bool = False,
) -> AiHoldAnalysisResponse:
    """The stored 存股 verdict for this snapshot, generating one only if absent.

    Takes the features and the checklist rather than raw rows: the caller has
    already built both for the free `/analysis/chen` response, and rebuilding
    them here would risk the model being asked about numbers the card never
    showed.
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

    if not force:
        existing = _find(db, features.sid, as_of, locale, model)
        if existing is not None:
            return _response(row=existing, features=features, rules=rules, cached=True)

    status = ai_service.quota_status(db, user)
    if status.used >= status.limit:
        raise ai_service.QuotaExceeded(status)

    generation = hold_gemini.generate(
        features=features, rules=rules, locale=locale, model=model
    )

    row = AiHoldAnalysis(
        sid=features.sid,
        as_of=as_of,
        model=model,
        prompt_version=hold_prompts.PROMPT_VERSION,
        locale=locale,
        suitability=generation.verdict.suitability,
        confidence=generation.verdict.confidence,
        headline=generation.verdict.headline[:500],
        reasons=generation.verdict.reasons,
        risks=generation.verdict.risks,
        agrees_with_rules=generation.verdict.agrees_with_rules,
        rule_score=rules.score,
        rule_suitability=rules.suitability,
        features=features.model_dump(mode="json"),
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
        db.execute(
            AiHoldAnalysis.__table__.delete().where(
                AiHoldAnalysis.sid == features.sid,
                AiHoldAnalysis.as_of == as_of,
                AiHoldAnalysis.model == model,
                AiHoldAnalysis.prompt_version == hold_prompts.PROMPT_VERSION,
                AiHoldAnalysis.locale == locale,
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
        existing = _find(db, features.sid, as_of, locale, model)
        if existing is None:
            raise
        logger.info("hold verdict raced for sid=%s as_of=%s", features.sid, as_of)
        return _response(row=existing, features=features, rules=rules, cached=True)

    db.refresh(row)
    return _response(row=row, features=features, rules=rules, cached=False)
