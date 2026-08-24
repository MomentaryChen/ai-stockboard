"""The Gemini adapter for the 存股 lane: transport only.

Prompt wording, the wire schema and `PROMPT_VERSION` live in `hold_prompts.py`
and `hold_deep_prompts.py`, the same split `gemini.py` and `prompts.py` use.
This module owns only the call: the SDK, the retry policy, the thinking budget
and the limiter. Both depths go through one transport, `generate_hold_verdict`,
because they return the same object from the same schema and differ only in
which instruction is sent and how large an answer is budgeted for -- two copies
of the retry loop would be two chances to fix a transport bug once.

It is a second adapter rather than another function in `gemini.py` because the
two lanes ask different questions and return different shapes, and because
keeping them apart is what stops a change to one prompt's transport from
quietly altering the other's. What they *do* share is deliberate and narrow:

* `gemini_throttle` -- one limiter, because it protects Gemini's view of this
  deployment. A second window would let the two lanes together spend twice
  what either was allowed alone.
* `MAX_ATTEMPTS`, `RETRYABLE`, `AiFailed`, `AiUnavailable`, `is_configured` --
  the failure vocabulary belongs to the provider, not to the prompt.

The failure this prompt guards is not the one `gemini.py` guards. There, the
risk is a model that always finds a trade. Here it is a model that fills in the
fundamentals it was not given -- it has read about these companies, and "台積電
has grown EPS for a decade" is a sentence it can produce with no data behind it
at all. That rule is spelled out in `hold_prompts.py`; the adapter only has to
deliver the instruction and schema intact.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import get_settings
from app.schemas import AiHoldVerdict, ChenRuleResult, HoldDeepInputs, HoldFeatures
from app.services.analysis import hold_deep_prompts, hold_prompts
from app.services.analysis.gemini import (
    MAX_ATTEMPTS,
    RETRYABLE,
    AiFailed,
    AiUnavailable,
    gemini_throttle,
    is_configured,
)

logger = logging.getLogger(__name__)

_settings = get_settings()

__all__ = [
    "AiFailed",
    "AiUnavailable",
    "Generation",
    "generate",
    "generate_deep",
    "is_configured",
]


@dataclass(frozen=True)
class Generation:
    verdict: AiHoldVerdict
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def _client():
    # Imported lazily and constructed per call: the SDK is only needed when the
    # feature is on, and a module-level client would be built at import time in
    # every process including the test run, where there is no key.
    from google import genai

    if not is_configured():
        raise AiUnavailable("GEMINI_API_KEY is not set")
    return genai.Client(api_key=_settings.gemini_api_key)


def _finish_reason(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no-candidates"
    return str(getattr(candidates[0], "finish_reason", "unknown"))


def generate(
    *,
    features: HoldFeatures,
    rules: ChenRuleResult,
    locale: str = "zh-TW",
    model: str | None = None,
) -> Generation:
    """Ask Gemini whether this is a company to hold. Blocks; run in a thread.

    `model` is the concrete id the caller already resolved (env default or the
    admin override). Falling back to the env default keeps unit tests that call
    this directly working without a database session -- same contract as
    `gemini.generate`.
    """
    return generate_hold_verdict(
        sid=features.sid,
        system_instruction=hold_prompts.system_instruction(locale),
        contents=hold_prompts.user_prompt(features=features, rules=rules),
        rules=rules,
        model=model,
    )


def generate_deep(
    *,
    features: HoldFeatures,
    deep: HoldDeepInputs,
    rules: ChenRuleResult,
    locale: str = "zh-TW",
    model: str | None = None,
) -> Generation:
    """The same question, shown the year-by-year record and the valuation band.

    A sibling of `generate` rather than a flag on it, for the reason
    `gemini.generate_deep` is one: the two send different instructions and
    budget different answer lengths, and a boolean that switches both is a
    function whose behaviour you have to read the body to know.

    Its own output ceiling, from the same settings the technical deep lane
    uses. A deep answer cites more numbers, and a verdict truncated mid-JSON
    fails the request outright rather than coming back shorter.
    """
    return generate_hold_verdict(
        sid=features.sid,
        system_instruction=hold_deep_prompts.system_instruction(locale),
        contents=hold_deep_prompts.user_prompt(
            features=features, deep=deep, rules=rules
        ),
        rules=rules,
        model=model,
        max_output_tokens=_settings.gemini_deep_max_output_tokens,
        thinking_budget=_settings.gemini_deep_thinking_budget,
    )


def generate_hold_verdict(
    *,
    sid: str,
    system_instruction: str,
    contents: str,
    rules: ChenRuleResult,
    model: str | None = None,
    max_output_tokens: int | None = None,
    thinking_budget: int | None = None,
) -> Generation:
    """Transport for any prompt whose answer is a `hold_prompts.WireVerdict`.

    Shared by this lane's two depths, exactly as `gemini.generate_verdict` is
    shared by the technical lane's. It stays separate from that one because the
    verdict shape genuinely differs -- a suitability and an agreement flag, not
    an action and a size -- which is the same line the two adapter modules were
    split along in the first place.

    `rules` is threaded through only because `to_verdict` needs it to decide
    whether "agrees with the checklist" is a claim anyone can make.
    """
    from google.genai import errors, types

    model_name = model or _settings.gemini_model
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=_settings.gemini_temperature,
        max_output_tokens=(
            _settings.gemini_max_output_tokens
            if max_output_tokens is None
            else max_output_tokens
        ),
        # Stated rather than left to the model's default, for the reason spelled
        # out in gemini.py: on the 2.5 models thinking is billed against
        # max_output_tokens, so an unstated budget can spend the whole
        # allowance before the JSON starts and return an empty body.
        thinking_config=types.ThinkingConfig(
            thinking_budget=(
                _settings.gemini_thinking_budget
                if thinking_budget is None
                else thinking_budget
            )
        ),
        response_mime_type="application/json",
        response_json_schema=hold_prompts.WireVerdict.model_json_schema(),
        http_options=types.HttpOptions(
            timeout=int(_settings.gemini_timeout_seconds * 1000)
        ),
    )

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        gemini_throttle.acquire()
        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=model_name, contents=contents, config=config
            )
            body = response.text
            if not body:
                raise AiFailed(
                    f"empty response (finish_reason={_finish_reason(response)})"
                )
            wire = hold_prompts.WireVerdict.model_validate_json(body)
        except errors.APIError as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            logger.warning(
                "hold gemini call failed sid=%s attempt=%d code=%s",
                sid, attempt + 1, code, exc_info=True,
            )
            if code not in RETRYABLE:
                break
            continue
        except Exception as exc:  # malformed, truncated or empty body
            last_error = exc
            logger.warning(
                "hold gemini returned unusable output sid=%s attempt=%d",
                sid, attempt + 1, exc_info=True,
            )
            continue

        usage = response.usage_metadata
        return Generation(
            verdict=hold_prompts.to_verdict(wire, rules),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=(
                getattr(usage, "candidates_token_count", None) if usage else None
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    raise AiFailed(f"Gemini did not return a usable hold verdict: {last_error}")
