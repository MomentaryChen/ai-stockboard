"""The 存股 Gemini call: a second prompt against the same provider.

Sibling of `gemini.py`, and the split is the point. That module asks for a
position call over days; this one asks whether a company is worth accumulating
and holding for its dividend over years. They share the SDK, the rate limiter
and the settings -- one provider, one deployment budget -- and share nothing
about what is asked or what comes back.

Specifically **not** shared:

* `SYSTEM_INSTRUCTION`. The technical rubric spends most of its length defining
  position size, which has no meaning here, and it forbids reasoning about the
  company, which is the entire subject here.
* `PROMPT_VERSION`. Prefixed `hold-` so the two version strings can never
  collide in an evaluation that reads both tables.
* The verdict shape. `enter`/`exit`/`hold` describes a trade; `strong`/`ok`/
  `weak`/`avoid` describes a company.

Shared on purpose: `gemini.gemini_throttle`. The limiter protects Gemini's view
of this deployment, and a second window would let the two lanes together spend
twice what either was allowed alone.

The failure that this prompt is written against is not the one `gemini.py`
guards. There, the risk is a model that always finds a trade. Here it is a
model that fills in the fundamentals it was not given -- it has read about
these companies, and "台積電 has grown EPS for a decade" is a sentence it can
produce with no data behind it at all. Hence the coverage rules below, and
hence `coverage_gaps` being handed over explicitly rather than left implied by
absent fields.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import AiHoldVerdict, ChenRuleResult, HoldFeatures
from app.services.analysis import chen_rules
from app.services.analysis.gemini import (
    MAX_ATTEMPTS,
    RETRYABLE,
    AiFailed,
    AiUnavailable,
    gemini_throttle,
    is_configured,
)

logger = logging.getLogger(__name__)

#: Bumped whenever the prompt, the rubric, or the shape of the features handed
#: over changes. Part of the `ai_hold_analysis` unique key, so a bump
#: re-generates on demand and keeps old verdicts attributable to the wording
#: that produced them. The `hold-` prefix keeps it distinguishable from the
#: technical lane's version at a glance and in a query.
PROMPT_VERSION = "hold-v1"

_settings = get_settings()

__all__ = [
    "PROMPT_VERSION",
    "AiFailed",
    "AiUnavailable",
    "Generation",
    "generate",
    "is_configured",
]


@dataclass(frozen=True)
class Generation:
    verdict: AiHoldVerdict
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class _WireVerdict(BaseModel):
    """What the model is asked to fill in.

    `agrees_with_rules` is a plain bool rather than nullable for the same
    reason `size` is a four-valued enum next door: a union in the response
    schema is one more thing for the API to get right. It is mapped to null
    here when the checklist had no verdict to agree with.
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


def _client():
    # Same lazy construction as the technical lane, for the same reason: the
    # SDK is only needed when the feature is on, and a module-level client
    # would be built at import time in the test run, where there is no key.
    from google import genai

    if not is_configured():
        raise AiUnavailable("GEMINI_API_KEY is not set")
    return genai.Client(api_key=_settings.gemini_api_key)


def _prompt(*, features: HoldFeatures, rules: ChenRuleResult) -> str:
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


def _finish_reason(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no-candidates"
    return str(getattr(candidates[0], "finish_reason", "unknown"))


def _to_verdict(wire: _WireVerdict, rules: ChenRuleResult) -> AiHoldVerdict:
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


def generate(
    *,
    features: HoldFeatures,
    rules: ChenRuleResult,
    locale: str = "zh-TW",
) -> Generation:
    """Ask Gemini whether this is a company to hold. Blocks; run in a thread."""
    from google.genai import errors, types

    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION.format(
            language=_LANGUAGE.get(locale, _LANGUAGE["zh-TW"])
        ),
        temperature=_settings.gemini_temperature,
        max_output_tokens=_settings.gemini_max_output_tokens,
        # Stated rather than left to the model's default, for the reason spelled
        # out in gemini.py: on the 2.5 models thinking is billed against
        # max_output_tokens, so an unstated budget can spend the whole
        # allowance before the JSON starts and return an empty body.
        thinking_config=types.ThinkingConfig(
            thinking_budget=_settings.gemini_thinking_budget
        ),
        response_mime_type="application/json",
        response_json_schema=_WireVerdict.model_json_schema(),
        http_options=types.HttpOptions(
            timeout=int(_settings.gemini_timeout_seconds * 1000)
        ),
    )
    contents = _prompt(features=features, rules=rules)

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        gemini_throttle.acquire()
        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=_settings.gemini_model, contents=contents, config=config
            )
            body = response.text
            if not body:
                raise AiFailed(
                    f"empty response (finish_reason={_finish_reason(response)})"
                )
            wire = _WireVerdict.model_validate_json(body)
        except errors.APIError as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            logger.warning(
                "hold gemini call failed sid=%s attempt=%d code=%s",
                features.sid, attempt + 1, code, exc_info=True,
            )
            if code not in RETRYABLE:
                break
            continue
        except Exception as exc:  # malformed, truncated or empty body
            last_error = exc
            logger.warning(
                "hold gemini returned unusable output sid=%s attempt=%d",
                features.sid, attempt + 1, exc_info=True,
            )
            continue

        usage = response.usage_metadata
        return Generation(
            verdict=_to_verdict(wire, rules),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=(
                getattr(usage, "candidates_token_count", None) if usage else None
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    raise AiFailed(f"Gemini did not return a usable hold verdict: {last_error}")
