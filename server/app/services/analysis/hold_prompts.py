"""Provider-agnostic prompt contract for the 存股 hold assessment.

Sibling of `prompts.py`, and split from the adapter for the same reason: what
the model is *asked* -- system wording, user turn, wire schema, and the version
that keys the cache -- belongs somewhere a provider swap does not touch.

It is a separate module rather than more functions in `prompts.py` because the
two contracts share nothing. That one asks what to do with a position over
days and spends most of its rubric defining position size; this one asks
whether a company is worth accumulating over years, where size has no meaning
at all. A single module would be two prompts in a trench coat, and the first
edit to either would risk the other.

Bump `PROMPT_VERSION` whenever the system instruction, the user turn, or the
wire schema changes -- and also when a supplied measurement changes what it
*means*, which is a subtler trigger worth naming. It is part of the
`ai_hold_analysis` unique key, so a bump re-generates on demand and keeps old
verdicts attributable to the inputs that produced them. The `hold-` prefix
keeps it from ever colliding with the technical lane's version in an
evaluation that reads both tables.

`hold-v2` was an example of the second kind: the wording was unchanged from
v1, but `trailing_pe` stopped being "last completed year's annual EPS against
today's close" and became the exchange's own daily published figure. A verdict
citing a PE has to stay attributable to which PE it was shown.

`hold-v3` adds `cape` to the measurements and changes what the Cheap
dimension means -- it now fails a company whose trailing PE is low only
because this year was the top of its cycle. A verdict that called such a name
cheap under v2 was reasoning from what it was given, and should not be
silently reissued as though it had seen the cyclical figure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import AiHoldVerdict, ChenRuleResult, HoldFeatures
from app.services.analysis import chen_rules

PROMPT_VERSION = "hold-v3"

SYSTEM_INSTRUCTION = """\
You are assessing whether one Taiwan-listed company suits a long-horizon
dividend-accumulation strategy: buy a good company when it is cheap, keep
buying it over years, reinvest what it pays. You are not judging a trade. A
price move over the next few weeks is irrelevant to every answer you give here.

ANSWER SHAPE

suitability  strong  Worth accumulating now. Earnings, payout record and price
                     all support it, and the record is long enough to trust.
             ok      Worth holding or accumulating slowly. Either one dimension
                     is weak, or the record is sound but the price is not.
             weak    Not now. The company may be sound but the case for buying
                     it as a dividend holding is not made by this data.
             avoid   The record argues against holding it at all.

RULES

1. Use only the supplied measurements and the rule engine's checklist. You have
   no news, no analyst estimates, no management commentary and no financial
   statements beyond the annual figures given.
2. You may know things about this company from elsewhere. Do not use them. If
   EPS or ROE is absent from the data, it is absent from your answer -- saying
   "profitable for a decade" about a company whose earnings you were not given
   is the single worst failure available to you here.
3. `coverage_gaps` lists what could not be checked. A non-empty list caps your
   confidence at "medium"; a gap covering earnings as well as valuation caps it
   at "low". Say in `risks` what is missing.
4. Every entry in `reasons` must cite a number you were given. "Cash dividend
   paid in 9 of the last 10 years, current yield 5.4%" is a reason; "a stable
   blue chip" is not.
5. Yield alone is not a reason to hold. A high yield on a falling price is a
   warning, and a long unbroken payout record at a thin yield is a bond
   substitute rather than an accumulation candidate. Say which one you are
   looking at.
6. You may disagree with the rule engine. If you do, set `agrees_with_rules`
   false and say in `reasons` what the checklist is missing or over-weighting.
   Agreement is not the goal; a checklist cannot see a payout funded by
   borrowing, and you may be able to infer that one is.
7. `risks` must name what would make holding this wrong -- a payout that
   outruns earnings, a single-customer business, a valuation that only works if
   the last year repeats -- not generic warnings about markets going down.
8. Confidence describes the evidence, not your enthusiasm.

Write every string in {language}. Be specific and brief; no disclaimers, no
preamble, no restating the question."""

_LANGUAGE = {
    "zh-TW": "Traditional Chinese (Taiwan)",
    "en": "English",
}


class WireVerdict(BaseModel):
    """What every provider asks the model to fill in.

    `agrees_with_rules` is a plain bool rather than nullable for the same
    reason `size` is a four-valued enum next door: a union in the response
    schema is one more thing for an API to get right. It is mapped to null in
    `to_verdict` when the checklist had no verdict to agree with.
    """

    suitability: Literal["strong", "ok", "weak", "avoid"]
    confidence: Literal["high", "medium", "low"]
    headline: str = Field(
        description="One sentence stating the verdict and its strongest justification."
    )
    reasons: list[str] = Field(
        description="2-4 findings, each citing a number from the supplied data."
    )
    risks: list[str] = Field(
        description="1-3 things that would make holding this a mistake."
    )
    agrees_with_rules: bool = Field(
        description="Whether your suitability matches the rule engine's."
    )


def language_name(locale: str) -> str:
    """The reviewed English name of the language to answer in.

    Public for the reason `prompts.language_name` is: the deep 存股 lane fills
    the same slot in its own instruction, and which languages a prompt has been
    reviewed in belongs to this package rather than to either lane.
    """
    return _LANGUAGE.get(locale, _LANGUAGE["zh-TW"])


def system_instruction(locale: str) -> str:
    """System turn with the reviewed language name filled in."""
    return SYSTEM_INSTRUCTION.format(language=language_name(locale))


def user_prompt(*, features: HoldFeatures, rules: ChenRuleResult) -> str:
    """The user turn: the snapshot as JSON, and the checklist that read it.

    The features go in as the same JSON the browser receives, so what the model
    saw and what the card shows cannot drift apart -- and so a stored verdict
    can be re-read against the exact numbers behind it.
    """
    return (
        f"Company: {features.sid} {features.name}\n"
        f"Industry (產業別): {features.industry or '(not listed)'}\n"
        f"Snapshot date: {features.as_of.isoformat() if features.as_of else '(no bars)'}\n\n"
        f"Measurements:\n{features.model_dump_json(indent=2)}\n\n"
        f"Rule engine (陳重銘存股檢查表) over the same snapshot:\n"
        f"{chen_rules.summarise(rules)}\n"
    )


def to_verdict(wire: WireVerdict, rules: ChenRuleResult) -> AiHoldVerdict:
    # The model is asked for a bool because the schema is simpler that way, but
    # agreement with a checklist that scored nothing is not a claim anyone can
    # make -- so it is dropped rather than recorded as a coin flip.
    agrees = wire.agrees_with_rules if rules.suitability is not None else None

    return AiHoldVerdict(
        suitability=wire.suitability,
        confidence=wire.confidence,
        headline=wire.headline.strip(),
        reasons=[r.strip() for r in wire.reasons if r.strip()],
        risks=[r.strip() for r in wire.risks if r.strip()],
        agrees_with_rules=agrees,
    )
