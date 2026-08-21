"""The Gemini adapter: transport only.

Prompt wording, the wire schema, and `PROMPT_VERSION` live in `prompts.py`.
This module owns the SDK import, retries, thinking budget, and rate limiter so
`ai.py` deals in `AiVerdict` and never sees a `google.genai` type. Replacing
the engine is a sibling adapter that imports the same prompts package.

Three constraints shape what is here.

**The output has to be a fixed shape.** Free text would have to be parsed, and a
parser for prose written by a model is a parser for prose written by a *future*
model too. The verdict is requested as JSON against the shared wire schema.

**The budget is shared and metered.** A Gemini request costs money and the quota
belongs to the deployment, not to the caller -- exactly the situation
`app/throttle.py` already exists for, so the same sliding window is reused with
its own numbers.

**`hold` has to be cheap to say.** That rule is spelled in `prompts.py`; the
adapter only has to deliver the instruction and schema intact.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import get_settings
from app.schemas import AiVerdict, BestFourPointResult, PriceFeatures
from app.services.analysis import prompts
from app.throttle import SlidingWindowThrottle

logger = logging.getLogger(__name__)

_settings = get_settings()

#: Same construction as `twse_throttle`, different budget. Both exist because an
#: upstream limit is a property of the deployment, not of one request.
gemini_throttle = SlidingWindowThrottle(
    max_calls=_settings.ai_throttle_max_calls,
    window_seconds=_settings.ai_throttle_window_seconds,
)

#: One retry only. A verdict is generated behind a button the user is waiting
#: on, and the failure modes worth retrying (a 503, a truncated JSON body) clear
#: immediately or not at all.
MAX_ATTEMPTS = 2

#: HTTP codes where trying again can plausibly help. Everything else -- a 400
#: because the schema was rejected, a 403 because the key is wrong -- will fail
#: identically the second time, and retrying it only doubles the wait the user
#: sits through before being told.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


class AiUnavailable(RuntimeError):
    """No API key configured. The feature is switched off, not broken."""


class AiFailed(RuntimeError):
    """The provider was reached and could not answer."""


@dataclass(frozen=True)
class Generation:
    verdict: AiVerdict
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def is_configured() -> bool:
    """Whether a key is present. The router turns 503 on this, not on a failure."""
    return bool(_settings.gemini_api_key)


def _client():
    # Imported lazily and constructed per call: the SDK is only needed when the
    # feature is on, and a module-level client would be built at import time in
    # every process including the test run, where there is no key.
    from google import genai

    if not is_configured():
        raise AiUnavailable("GEMINI_API_KEY is not set")
    return genai.Client(api_key=_settings.gemini_api_key)


def _finish_reason(response) -> str:
    """Why the model stopped, for the log line that explains an empty body.

    Defensive about the shape because a response with no text is exactly the
    case where the candidate list may also be missing.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no-candidates"
    return str(getattr(candidates[0], "finish_reason", "unknown"))


def generate(
    *,
    sid: str,
    name: str,
    features: PriceFeatures,
    traditional: BestFourPointResult,
    locale: str = "zh-TW",
) -> Generation:
    """Ask Gemini for a position call. Blocks; callers run in the threadpool."""
    from google.genai import errors, types

    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=prompts.system_instruction(locale),
        temperature=_settings.gemini_temperature,
        max_output_tokens=_settings.gemini_max_output_tokens,
        # Set explicitly rather than left to the model's default. On the 2.5
        # models thinking is on unless told otherwise and its tokens come out of
        # max_output_tokens, so the default risks spending the budget before the
        # JSON starts -- which arrives here as an empty body, not as a worse
        # verdict. See GEMINI_THINKING_BUDGET.
        thinking_config=types.ThinkingConfig(
            thinking_budget=_settings.gemini_thinking_budget
        ),
        response_mime_type="application/json",
        response_json_schema=prompts.WireVerdict.model_json_schema(),
        http_options=types.HttpOptions(
            timeout=int(_settings.gemini_timeout_seconds * 1000)
        ),
    )
    contents = prompts.user_prompt(
        sid=sid, name=name, features=features, traditional=traditional
    )

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
                # No text at all: the budget ran out mid-thought, or a safety
                # filter dropped the candidate. Named rather than left to
                # surface as "expected str, got None", because the two have
                # different fixes and only the first is ours.
                raise AiFailed(
                    f"empty response (finish_reason={_finish_reason(response)})"
                )
            wire = prompts.WireVerdict.model_validate_json(body)
        except errors.APIError as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            logger.warning(
                "gemini call failed sid=%s attempt=%d code=%s",
                sid, attempt + 1, code, exc_info=True,
            )
            if code not in RETRYABLE:
                break
            continue
        except Exception as exc:  # malformed, truncated or empty body
            last_error = exc
            logger.warning(
                "gemini returned unusable output sid=%s attempt=%d",
                sid, attempt + 1, exc_info=True,
            )
            continue

        usage = response.usage_metadata
        return Generation(
            verdict=prompts.to_verdict(wire),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=(
                getattr(usage, "candidates_token_count", None) if usage else None
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    raise AiFailed(f"Gemini did not return a usable verdict: {last_error}")
