"""The Gemini adapter for the 存股 lane: transport only.

Prompt wording, the wire schema and `PROMPT_VERSION` live in `hold_prompts.py`,
the same split `gemini.py` and `prompts.py` use. This module owns only the
call: the SDK, the retry policy, the thinking budget and the limiter.

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
from app.schemas import AiHoldVerdict, ChenRuleResult, HoldFeatures
from app.services.analysis import hold_prompts
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
    from google.genai import errors, types

    model_name = model or _settings.gemini_model
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=hold_prompts.system_instruction(locale),
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
        response_json_schema=hold_prompts.WireVerdict.model_json_schema(),
        http_options=types.HttpOptions(
            timeout=int(_settings.gemini_timeout_seconds * 1000)
        ),
    )
    contents = hold_prompts.user_prompt(features=features, rules=rules)

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
            verdict=hold_prompts.to_verdict(wire, rules),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=(
                getattr(usage, "candidates_token_count", None) if usage else None
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    raise AiFailed(f"Gemini did not return a usable hold verdict: {last_error}")
