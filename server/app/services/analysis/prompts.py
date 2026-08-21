"""Provider-agnostic prompt contract for the AI position call.

Providers (Gemini today, Claude or others later) own transport, retries, and
rate limits. Everything the model is *asked* -- system wording, user turn,
structured output schema, and the version that keys the cache -- lives here so
swapping an engine is a new adapter, not a prompt rewrite.

Bump `PROMPT_VERSION` whenever the system instruction, user turn shape, or
wire schema changes. It is part of the `ai_analysis` unique key: a bump
invalidates nothing and re-generates on demand, and keeps old verdicts
attributable to the wording that produced them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import AiVerdict, BestFourPointResult, PriceFeatures

PROMPT_VERSION = "v1"

SYSTEM_INSTRUCTION = """\
You are a disciplined technical analyst reading one Taiwan-listed instrument.

You will be given a set of derived measurements for a single trading day, plus
the verdict a deterministic rule engine reached from the same bars. Decide what
a holder should do with their position.

ANSWER SHAPE

action  enter  open or add to a position
        exit   close or reduce a position
        hold   leave the position alone

size    How much of a position the action applies to. Use "none" when, and only
        when, action is "hold".

        large   Trend, volume and price location all point the same way and the
                risks you can name are minor. This is the rare case; do not
                reach for it because the move looks obvious.
        medium  The balance of evidence points one way and you can state the
                counter-evidence.
        small   A probe or a trim. The signal is thin, volatility is high, or
                price sits at an extreme where being wrong is expensive.

RULES

1. Use only the measurements supplied. You have no news, no earnings, no
   institutional flow and no broker data. Do not invent any, and do not reason
   from what you remember about this company.
2. "hold" is a correct and expected answer. Mixed evidence is a reason to hold,
   and so is a move that has already happened. Do not manufacture a trade.
3. Every entry in `reasons` must cite a number you were given. "Volume is 1.8x
   its 5-day average" is a reason; "momentum looks strong" is not.
4. `risks` must name what would make this call wrong, not generic warnings about
   market conditions.
5. You may agree or disagree with the rule engine. If you disagree, say so in
   `reasons` and say what it is missing.
6. Confidence describes the evidence, not your enthusiasm. Thin or contradictory
   measurements mean low confidence even when the action seems clear.

Write every string in {language}. Be specific and brief; no disclaimers, no
preamble, no restating the question."""

_LANGUAGE = {
    "zh-TW": "Traditional Chinese (Taiwan)",
    "en": "English",
}


class WireVerdict(BaseModel):
    """What every provider asks the model to fill in.

    Kept separate from `AiVerdict` on purpose. `size` is a plain enum including
    "none" rather than a nullable field: a union in the response schema is one
    more thing for an API to get right, and mapping "none" to null here costs a
    single line. The public shape stays null-when-hold regardless of what any
    provider prefers to emit.
    """

    action: Literal["enter", "exit", "hold"]
    size: Literal["large", "medium", "small", "none"]
    confidence: Literal["high", "medium", "low"]
    headline: str = Field(description="One sentence stating the call and its single strongest justification.")
    reasons: list[str] = Field(description="2-4 findings, each citing a number from the supplied data.")
    risks: list[str] = Field(description="1-3 things that would make this call wrong.")


def system_instruction(locale: str) -> str:
    """System turn with the reviewed language name filled in."""
    return SYSTEM_INSTRUCTION.format(
        language=_LANGUAGE.get(locale, _LANGUAGE["zh-TW"])
    )


def user_prompt(
    *,
    sid: str,
    name: str,
    features: PriceFeatures,
    traditional: BestFourPointResult,
) -> str:
    """The user turn: the facts, as JSON, with nothing inferred.

    The measurements go in as the same JSON the API returns to the browser, so
    what the model saw and what the card shows cannot drift apart.
    """
    return (
        f"Instrument: {sid} {name}\n"
        f"Trading day: {features.as_of.isoformat()}\n\n"
        f"Measurements:\n{features.model_dump_json(indent=2)}\n\n"
        f"Rule engine (四大買賣點) verdict for the same bars:\n"
        f"  signal: {traditional.signal}\n"
        f"  label: {traditional.label}\n"
        f"  reasons: {'; '.join(traditional.reasons) or '(none given)'}\n"
    )


def to_verdict(wire: WireVerdict) -> AiVerdict:
    # The schema lets the model emit a size alongside hold, or "none" alongside
    # enter. The database has a CHECK constraint for the same pair of mistakes;
    # this is where they are corrected rather than rejected, because a usable
    # answer with an inconsistent size is not worth failing the request over.
    size = None if wire.action == "hold" else wire.size
    if size == "none":
        size = "small"

    return AiVerdict(
        action=wire.action,
        size=size,
        confidence=wire.confidence,
        headline=wire.headline.strip(),
        reasons=[r.strip() for r in wire.reasons if r.strip()],
        risks=[r.strip() for r in wire.risks if r.strip()],
    )
