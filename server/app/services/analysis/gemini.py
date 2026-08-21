"""The Gemini call, and the only module that knows a provider exists.

Everything provider-specific is behind `generate()`: the SDK import, the prompt,
the wire schema, the retry policy and the rate limiter. `ai.py` above it deals
in `AiVerdict` and never sees a `google.genai` type, so replacing the engine is
a rewrite of this file rather than a search across the service.

Three constraints shape what is here.

**The output has to be a fixed shape.** Free text would have to be parsed, and a
parser for prose written by a model is a parser for prose written by a *future*
model too. The verdict is requested as JSON against a schema the API enforces.

**The budget is shared and metered.** A Gemini request costs money and the quota
belongs to the deployment, not to the caller -- exactly the situation
`app/throttle.py` already exists for, so the same sliding window is reused with
its own numbers.

**`hold` has to be cheap to say.** A model asked for a recommendation will
produce one; twstock's 四大買賣點 never returned Don't touch across 20 000 draws
and that was the bug this project was built to document. The prompt names hold
as a correct answer and the rubric gives it conditions, rather than leaving it
as the option the model reaches for only when it has nothing at all.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import AiVerdict, BestFourPointResult, PriceFeatures
from app.throttle import SlidingWindowThrottle

logger = logging.getLogger(__name__)

#: Bumped whenever the prompt or the rubric below changes. It is part of the
#: `ai_analysis` unique key, so a bump invalidates nothing and re-generates on
#: demand -- and, more to the point, keeps old verdicts attributable to the
#: wording that produced them.
PROMPT_VERSION = "v1"

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


class _WireVerdict(BaseModel):
    """What the model is asked to fill in.

    Kept separate from `AiVerdict` on purpose. `size` is a plain enum including
    "none" rather than a nullable field: a union in the response schema is one
    more thing for the API to get right, and mapping "none" to null here costs a
    single line. The public shape stays null-when-hold regardless of what any
    provider prefers to emit.
    """

    action: Literal["enter", "exit", "hold"]
    size: Literal["large", "medium", "small", "none"]
    confidence: Literal["high", "medium", "low"]
    headline: str = Field(description="One sentence stating the call and its single strongest justification.")
    reasons: list[str] = Field(description="2-4 findings, each citing a number from the supplied data.")
    risks: list[str] = Field(description="1-3 things that would make this call wrong.")


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


def _prompt(
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


def _finish_reason(response) -> str:
    """Why the model stopped, for the log line that explains an empty body.

    Defensive about the shape because a response with no text is exactly the
    case where the candidate list may also be missing.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no-candidates"
    return str(getattr(candidates[0], "finish_reason", "unknown"))


def _to_verdict(wire: _WireVerdict) -> AiVerdict:
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


def generate(
    *,
    sid: str,
    name: str,
    features: PriceFeatures,
    traditional: BestFourPointResult,
    locale: str = "zh-TW",
    model: str | None = None,
) -> Generation:
    """Ask Gemini for a position call. Blocks; callers run in the threadpool.

    `model` is the concrete id the caller already resolved (env default or the
    admin override). Falling back to the env default keeps unit tests that call
    this directly working without a database session.
    """
    from google.genai import errors, types

    model_name = model or _settings.gemini_model
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION.format(
            language=_LANGUAGE.get(locale, _LANGUAGE["zh-TW"])
        ),
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
        response_json_schema=_WireVerdict.model_json_schema(),
        http_options=types.HttpOptions(
            timeout=int(_settings.gemini_timeout_seconds * 1000)
        ),
    )
    contents = _prompt(sid=sid, name=name, features=features, traditional=traditional)

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
                # No text at all: the budget ran out mid-thought, or a safety
                # filter dropped the candidate. Named rather than left to
                # surface as "expected str, got None", because the two have
                # different fixes and only the first is ours.
                raise AiFailed(
                    f"empty response (finish_reason={_finish_reason(response)})"
                )
            wire = _WireVerdict.model_validate_json(body)
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
            verdict=_to_verdict(wire),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=(
                getattr(usage, "candidates_token_count", None) if usage else None
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    raise AiFailed(f"Gemini did not return a usable verdict: {last_error}")
