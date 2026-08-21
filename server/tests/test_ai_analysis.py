"""Pins the parts of the AI engine that must not drift, without a network call.

The generated prose cannot be asserted on -- that is the nature of the feature.
What can be asserted on is everything around it, and that is where the failures
that matter live:

  * the prompt still tells the model that `hold` is a correct answer. twstock's
    四大買賣點 never returned Don't touch in 20 000 draws because a gate had been
    dropped; an engine that always finds a trade has that same defect, and here
    it would be one deleted sentence away.
  * `action`/`size` stay consistent, because the database has a CHECK constraint
    that rejects the combinations the card cannot render.
  * the prompt carries the measurements *and* the rule engine's verdict, since
    a comparison the model never saw is not a comparison.
  * a provider failure raises rather than returning something plausible.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.models import DailyPrice
from app.schemas import BestFourPointResult
from app.services.analysis import ai, features, gemini, prompts

SID = "2330"
START = datetime.date(2024, 1, 2)


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch):
    """The real limiter would sleep 60s once a test makes its sixth call."""
    monkeypatch.setattr(gemini.gemini_throttle, "acquire", lambda: None)


def _rows(n: int = 40) -> list[DailyPrice]:
    return [
        DailyPrice(
            sid=SID,
            date=START + datetime.timedelta(days=i),
            open=50.0,
            close=50.0 + (i % 5),
            high=56.0,
            low=48.0,
            capacity=10_000 + i,
        )
        for i in range(n)
    ]


def _features():
    extracted = features.extract(_rows())
    assert extracted is not None
    return extracted


def _traditional() -> BestFourPointResult:
    return BestFourPointResult(
        signal="hold", label="Don't touch", reasons=["四大買點條件皆不符合"]
    )


def _fake_client(payload: dict, *, usage=None, fail_times: int = 0, raises=None):
    """A stand-in for genai.Client with a scripted response.

    `calls` counts attempts and keeps the last config, so a test can assert on
    what was asked for as well as on what came back.
    """
    calls = {"n": 0, "config": None}

    def generate_content(*, model, contents, config):  # noqa: ARG001
        calls["n"] += 1
        calls["config"] = config
        if raises is not None:
            raise raises
        if calls["n"] <= fail_times:
            raise ValueError("truncated body")
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=usage)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


_VALID = {
    "action": "enter",
    "size": "medium",
    "confidence": "medium",
    "headline": "量能放大且站上季線，可分批進場。",
    "reasons": ["成交量為 5 日均量的 1.8 倍", "收盤高於 MA20 3.2%"],
    "risks": ["位階已在 60 日區間的 85%"],
}


# --- the prompt ---------------------------------------------------------------


def test_system_instruction_still_licenses_hold():
    """The one sentence whose deletion would recreate the twstock defect."""
    text = prompts.SYSTEM_INSTRUCTION
    assert '"hold" is a correct and expected answer' in text
    assert "Do not manufacture a trade" in text


def test_system_instruction_defines_every_size_the_ui_renders():
    text = prompts.SYSTEM_INSTRUCTION
    for size in ("large", "medium", "small"):
        assert f"{size}   " in text or f"{size}  " in text, size


def test_system_instruction_forbids_knowledge_the_model_was_not_given():
    """Nothing here supplies news or fundamentals, so nothing may be cited."""
    assert "Use only the measurements supplied" in prompts.SYSTEM_INSTRUCTION


def test_prompt_carries_both_the_measurements_and_the_rule_verdict():
    prompt = prompts.user_prompt(
        sid=SID, name="台積電", features=_features(), traditional=_traditional()
    )

    assert SID in prompt and "台積電" in prompt
    # The measurements go in as the same JSON the browser receives.
    assert '"alignment"' in prompt and '"position_pct"' in prompt
    # ...and so does the verdict the model is invited to disagree with.
    assert "Don't touch" in prompt
    assert "四大買點條件皆不符合" in prompt


# --- the wire contract --------------------------------------------------------


def test_wire_schema_is_json_schema_serialisable_with_closed_enums():
    """What every provider is handed as the structured-output schema."""
    schema = prompts.WireVerdict.model_json_schema()
    json.dumps(schema)  # must not raise

    props = schema["properties"]
    assert set(props["action"]["enum"]) == {"enter", "exit", "hold"}
    # "none" rather than a nullable union -- see WireVerdict's docstring.
    assert set(props["size"]["enum"]) == {"large", "medium", "small", "none"}
    assert set(props["confidence"]["enum"]) == {"high", "medium", "low"}


def test_hold_never_carries_a_size_and_a_trade_always_does():
    """Mirrors ck_ai_analysis_size_matches_action, corrected rather than rejected."""
    held = prompts.to_verdict(
        prompts.WireVerdict(**{**_VALID, "action": "hold", "size": "large"})
    )
    assert held.action == "hold"
    assert held.size is None

    probed = prompts.to_verdict(
        prompts.WireVerdict(**{**_VALID, "action": "exit", "size": "none"})
    )
    assert probed.action == "exit"
    assert probed.size == "small"  # the most cautious reading of "unspecified"


def test_blank_reasons_and_risks_are_dropped_not_rendered():
    verdict = prompts.to_verdict(
        prompts.WireVerdict(**{**_VALID, "reasons": ["  ", "量能放大"], "risks": [""]})
    )
    assert verdict.reasons == ["量能放大"]
    assert verdict.risks == []


# --- the call -----------------------------------------------------------------


def test_generate_returns_the_verdict_and_records_what_it_cost(monkeypatch):
    usage = SimpleNamespace(prompt_token_count=1234, candidates_token_count=210)
    client, calls = _fake_client(_VALID, usage=usage)
    monkeypatch.setattr(gemini, "_client", lambda: client)

    result = gemini.generate(
        sid=SID, name="台積電", features=_features(), traditional=_traditional()
    )

    assert calls["n"] == 1
    assert result.verdict.action == "enter"
    assert result.verdict.size == "medium"
    assert result.input_tokens == 1234
    assert result.output_tokens == 210
    assert result.latency_ms >= 0


def test_generate_retries_once_then_gives_up_loudly(monkeypatch):
    """A bad body must not become a confident-looking verdict."""
    client, calls = _fake_client(_VALID, fail_times=1)
    monkeypatch.setattr(gemini, "_client", lambda: client)
    recovered = gemini.generate(
        sid=SID, name="台積電", features=_features(), traditional=_traditional()
    )
    assert calls["n"] == 2
    assert recovered.verdict.action == "enter"

    client, calls = _fake_client(_VALID, fail_times=99)
    monkeypatch.setattr(gemini, "_client", lambda: client)
    with pytest.raises(gemini.AiFailed):
        gemini.generate(
            sid=SID, name="台積電", features=_features(), traditional=_traditional()
        )
    assert calls["n"] == gemini.MAX_ATTEMPTS


def test_thinking_budget_is_always_stated_rather_than_left_to_the_model(monkeypatch):
    """The 2.5 models think by default and bill it to max_output_tokens.

    Left implicit, a model can spend the whole budget reasoning and return an
    empty body -- a failed request, not a weaker answer. The budget being
    present at all is the assertion; its value is a tuning knob.
    """
    client, calls = _fake_client(_VALID)
    monkeypatch.setattr(gemini, "_client", lambda: client)
    gemini.generate(
        sid=SID, name="台積電", features=_features(), traditional=_traditional()
    )

    thinking = calls["config"].thinking_config
    assert thinking is not None
    assert thinking.thinking_budget == gemini._settings.gemini_thinking_budget


def test_an_empty_body_fails_as_itself_rather_than_as_a_parse_error(monkeypatch):
    """No text means the budget ran out or a filter fired, not bad JSON.

    Both end in AiFailed, but only one of them is ours to fix, so the finish
    reason has to survive into the message an operator will read.
    """
    def generate_content(*, model, contents, config):  # noqa: ARG001
        return SimpleNamespace(
            text=None,
            usage_metadata=None,
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        )

    monkeypatch.setattr(
        gemini,
        "_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    with pytest.raises(gemini.AiFailed, match="MAX_TOKENS"):
        gemini.generate(
            sid=SID, name="台積電", features=_features(), traditional=_traditional()
        )


def test_a_rejected_request_is_not_retried(monkeypatch):
    """A 400 will be a 400 again; retrying only doubles the wait before failing."""
    rejected = errors.APIError(400, {"error": {"message": "bad schema"}})
    client, calls = _fake_client(_VALID, raises=rejected)
    monkeypatch.setattr(gemini, "_client", lambda: client)

    with pytest.raises(gemini.AiFailed):
        gemini.generate(
            sid=SID, name="台積電", features=_features(), traditional=_traditional()
        )
    assert calls["n"] == 1

    # ...whereas an overloaded backend gets the second chance it deserves.
    overloaded = errors.APIError(503, {"error": {"message": "overloaded"}})
    client, calls = _fake_client(_VALID, raises=overloaded)
    monkeypatch.setattr(gemini, "_client", lambda: client)

    with pytest.raises(gemini.AiFailed):
        gemini.generate(
            sid=SID, name="台積電", features=_features(), traditional=_traditional()
        )
    assert calls["n"] == gemini.MAX_ATTEMPTS


def test_generate_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.setattr(gemini._settings, "gemini_api_key", "")
    assert gemini.is_configured() is False
    with pytest.raises(gemini.AiUnavailable):
        gemini._client()


# --- orchestration ------------------------------------------------------------


def test_insufficient_history_is_refused_before_anything_is_spent(monkeypatch):
    """The quota and the provider must not be reached for an unanswerable stock."""
    def _explode():
        raise AssertionError("no client should be built")

    monkeypatch.setattr(gemini, "_client", _explode)
    short = _rows(features.MIN_SAMPLES_FOR_AI - 1)

    with pytest.raises(ai.InsufficientData):
        ai.get_or_create(
            db=None, sid=SID, name="台積電", rows=short, user=SimpleNamespace(id=1)
        )


def test_unknown_locales_fall_back_rather_than_being_sent_through():
    """Asking Gemini for a language nobody reviewed is worse than defaulting."""
    assert ai.normalise_locale("en") == "en"
    assert ai.normalise_locale("zh-TW") == "zh-TW"
    assert ai.normalise_locale("ja") == ai.DEFAULT_LOCALE
    assert ai.normalise_locale(None) == ai.DEFAULT_LOCALE


def test_prompt_version_is_pinned_so_stored_verdicts_stay_attributable():
    """Changing the prompt without bumping this silently mixes two engines."""
    assert prompts.PROMPT_VERSION == "v1"
