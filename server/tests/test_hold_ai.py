"""Pins the 存股 AI lane's separation from the technical one, without a network call.

The prose cannot be asserted on. What can, and where the damage would be:

  * the prompt still forbids the model from supplying fundamentals it was not
    given. This lane hands over a feature set that is mostly empty until the
    ingest lands, and a model that fills the blanks from memory produces a
    confident, checkable-looking, entirely fabricated answer -- the failure
    mode this feature has that the technical one does not;
  * the two lanes do not share a prompt version, a table, or a vocabulary, so
    an evaluation reading both cannot average two engines together;
  * the daily quota is one allowance across both, because two would silently
    double what every existing account may spend;
  * the checklist reaches the model *with* its evidence, since a comparison the
    model never saw is not a comparison.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.schemas import (
    HoldDividendFeatures,
    HoldFeatures,
    HoldFundamentalsFeatures,
    HoldLiquidityFeatures,
    HoldPriceFeatures,
)
from app.services.analysis import (
    ai,
    chen_rules,
    gemini,
    hold_ai,
    hold_gemini,
    hold_prompts,
    prompts,
)

SID = "2880"


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch):
    """The shared limiter would sleep 60s once a test makes its sixth call."""
    monkeypatch.setattr(hold_gemini.gemini_throttle, "acquire", lambda: None)


def _features(*, covered: bool = True) -> HoldFeatures:
    """A well-covered financial holding, or the same name with no fundamentals."""
    return HoldFeatures(
        sid=SID,
        name="華南金",
        as_of=datetime.date(2026, 3, 20),
        industry="金融保險業",
        dividend=HoldDividendFeatures(
            coverage="history",
            window_years=10,
            years_observed=10,
            years_with_cash=10,
            consecutive_years_with_cash=10,
            ttm_cash=1.08,
            cash_yield_pct=5.4,
            avg_cash_per_year=0.95,
            avg_yield_pct=4.75,
            latest_ex_date=datetime.date(2025, 8, 15),
            years_with_stock_dividend=6,
        ),
        liquidity=HoldLiquidityFeatures(
            trading_days=60,
            avg_daily_shares=18_000_000,
            avg_daily_turnover=360_000_000,
            no_trade_days=0,
        ),
        fundamentals=HoldFundamentalsFeatures(
            years_available=10 if covered else 0,
            eps_years_checked=10 if covered else 0,
            eps_positive_years=10 if covered else None,
            latest_eps=1.5 if covered else None,
            latest_eps_year=2025 if covered else None,
            avg_eps=1.4 if covered else None,
            avg_roe_pct=11.2 if covered else None,
            roe_stdev_pct=1.4 if covered else None,
            roe_years_checked=10 if covered else 0,
            trailing_pe=13.3 if covered else None,
        ),
        price=HoldPriceFeatures(
            latest_close=20.0,
            window_high=24.5,
            window_low=16.2,
            position_pct=45.8,
            drawdown_from_high_pct=-18.4,
            return_1y_pct=3.2,
        ),
    )


def _rules(*, covered: bool = True):
    return chen_rules.evaluate(_features(covered=covered))


def _fake_client(payload: dict, *, usage=None, fail_times: int = 0, raises=None):
    calls = {"n": 0, "config": None, "contents": None}

    def generate_content(*, model, contents, config):  # noqa: ARG001
        calls["n"] += 1
        calls["config"] = config
        calls["contents"] = contents
        if raises is not None:
            raise raises
        if calls["n"] <= fail_times:
            raise ValueError("truncated body")
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=usage)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


_VALID = {
    "suitability": "ok",
    "confidence": "medium",
    "headline": "配息連續十年、殖利率 5.4%，但本益比 13.3 高於金融股的常見區間。",
    "reasons": ["近十年年年配發現金", "現金殖利率 5.4%", "近十年 ROE 平均 11.2%"],
    "risks": ["六年配過股票股利，稀釋未反映在現金殖利率"],
    "agrees_with_rules": True,
}


# --- the two lanes stay apart -------------------------------------------------


def test_the_hold_prompt_is_not_the_technical_prompt():
    """Sharing one would ask a decade-horizon question with a day-trade rubric."""
    assert hold_prompts.SYSTEM_INSTRUCTION != prompts.SYSTEM_INSTRUCTION
    # Position sizing has no meaning over a ten-year hold.
    for word in ("enter", "exit", "size"):
        assert word not in hold_prompts.SYSTEM_INSTRUCTION.lower().split()


def test_prompt_versions_cannot_collide_between_lanes():
    """An evaluation reading both tables must never average two engines."""
    assert hold_prompts.PROMPT_VERSION.startswith("hold-")
    assert hold_prompts.PROMPT_VERSION != prompts.PROMPT_VERSION


def test_both_lanes_share_one_rate_limiter():
    """Two windows would let them together spend twice either one's budget."""
    assert hold_gemini.gemini_throttle is gemini.gemini_throttle


# --- the prompt ---------------------------------------------------------------


def test_the_prompt_forbids_fundamentals_the_model_was_not_given():
    """The failure mode unique to this lane: a model filling in what it recalls."""
    text = hold_prompts.SYSTEM_INSTRUCTION
    assert "Do not use them" in text
    assert "coverage_gaps" in text


def test_the_prompt_frames_the_horizon_as_years_not_days():
    # Rewrapped rather than matched line by line: the rubric is hard-wrapped,
    # so a phrase that spans a break is still one phrase.
    text = " ".join(hold_prompts.SYSTEM_INSTRUCTION.split())
    assert "long-horizon dividend-accumulation strategy" in text
    assert "You are not judging a trade" in text


def test_the_prompt_refuses_yield_as_a_reason_on_its_own():
    """The trap this method walks into: a collapsing price lifts the yield."""
    assert "Yield alone is not a reason to hold" in hold_prompts.SYSTEM_INSTRUCTION


def test_the_prompt_carries_the_measurements_and_the_checklist_with_its_evidence():
    features = _features()
    rules = _rules()
    prompt = hold_prompts.user_prompt(features=features, rules=rules)

    assert SID in prompt and "華南金" in prompt
    # The measurements go in as the JSON the browser receives.
    assert '"consecutive_years_with_cash"' in prompt
    assert '"trailing_pe"' in prompt
    # ...and so does every line of the verdict the model may disagree with.
    for dimension in rules.dimensions:
        assert dimension.key in prompt
        assert dimension.evidence in prompt


def test_an_uncovered_company_reaches_the_model_with_its_gaps_named():
    """Silence about missing EPS is exactly what invites the model to invent it."""
    rules = _rules(covered=False)
    prompt = hold_prompts.user_prompt(features=_features(covered=False), rules=rules)

    assert "no_annual_fundamentals" in prompt
    assert "unknown" in prompt


# --- the wire contract --------------------------------------------------------


def test_wire_schema_is_json_schema_serialisable_with_closed_enums():
    schema = hold_prompts.WireVerdict.model_json_schema()
    json.dumps(schema)  # must not raise

    props = schema["properties"]
    assert set(props["suitability"]["enum"]) == {"strong", "ok", "weak", "avoid"}
    assert set(props["confidence"]["enum"]) == {"high", "medium", "low"}
    # No enter/exit/size anywhere: the two lanes share no vocabulary.
    assert "action" not in props and "size" not in props


def test_agreement_with_an_unscored_checklist_is_dropped_not_recorded():
    """Nobody can agree with a verdict that was never reached."""
    unscored = chen_rules.evaluate(
        _features(covered=False).model_copy(
            update={
                "dividend": _features().dividend.model_copy(
                    update={"coverage": "recent", "years_with_cash": 0}
                ),
                "liquidity": _features().liquidity.model_copy(
                    update={"trading_days": 0, "avg_daily_shares": None}
                ),
            }
        )
    )
    assert unscored.suitability is None

    verdict = hold_prompts.to_verdict(
        hold_prompts.WireVerdict(**_VALID), unscored
    )
    assert verdict.agrees_with_rules is None

    # ...and is kept whenever there was something to agree with.
    kept = hold_prompts.to_verdict(hold_prompts.WireVerdict(**_VALID), _rules())
    assert kept.agrees_with_rules is True


def test_blank_reasons_and_risks_are_dropped_not_rendered():
    verdict = hold_prompts.to_verdict(
        hold_prompts.WireVerdict(**{**_VALID, "reasons": ["  ", "連十年配息"], "risks": [""]}),
        _rules(),
    )
    assert verdict.reasons == ["連十年配息"]
    assert verdict.risks == []


# --- the call -----------------------------------------------------------------


def test_generate_returns_the_verdict_and_records_what_it_cost(monkeypatch):
    usage = SimpleNamespace(prompt_token_count=2100, candidates_token_count=180)
    client, calls = _fake_client(_VALID, usage=usage)
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)

    result = hold_gemini.generate(features=_features(), rules=_rules())

    assert calls["n"] == 1
    assert result.verdict.suitability == "ok"
    assert result.input_tokens == 2100
    assert result.output_tokens == 180
    assert result.latency_ms >= 0


def test_generate_retries_once_then_gives_up_loudly(monkeypatch):
    client, calls = _fake_client(_VALID, fail_times=1)
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)
    assert hold_gemini.generate(features=_features(), rules=_rules()).verdict
    assert calls["n"] == 2

    client, calls = _fake_client(_VALID, fail_times=99)
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)
    with pytest.raises(hold_gemini.AiFailed):
        hold_gemini.generate(features=_features(), rules=_rules())
    assert calls["n"] == gemini.MAX_ATTEMPTS


def test_a_rejected_request_is_not_retried(monkeypatch):
    rejected = errors.APIError(400, {"error": {"message": "bad schema"}})
    client, calls = _fake_client(_VALID, raises=rejected)
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)

    with pytest.raises(hold_gemini.AiFailed):
        hold_gemini.generate(features=_features(), rules=_rules())
    assert calls["n"] == 1


def test_an_empty_body_fails_as_itself_rather_than_as_a_parse_error(monkeypatch):
    def generate_content(*, model, contents, config):  # noqa: ARG001
        return SimpleNamespace(
            text=None,
            usage_metadata=None,
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        )

    monkeypatch.setattr(
        hold_gemini,
        "_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )
    with pytest.raises(hold_gemini.AiFailed, match="MAX_TOKENS"):
        hold_gemini.generate(features=_features(), rules=_rules())


# --- orchestration ------------------------------------------------------------


def test_a_stock_with_no_bars_is_refused_before_anything_is_spent(monkeypatch):
    """No trading day means no cache key, so a verdict could not be stored."""
    def _explode():
        raise AssertionError("no client should be built")

    monkeypatch.setattr(hold_gemini, "_client", _explode)
    no_bars = _features().model_copy(update={"as_of": None})

    with pytest.raises(hold_ai.InsufficientData):
        hold_ai.get_or_create(
            db=None,
            features=no_bars,
            rules=_rules(),
            user=SimpleNamespace(id=1),
        )


def test_unknown_locales_fall_back_rather_than_being_sent_through():
    assert hold_ai.normalise_locale("en") == "en"
    assert hold_ai.normalise_locale("ja") == hold_ai.DEFAULT_LOCALE


def test_the_daily_allowance_is_one_budget_across_both_lanes():
    """Two counters would mean shipping this lane doubled every account's spend."""
    import inspect

    source = inspect.getsource(ai.quota_status)
    assert "AiHoldAnalysis" in source
    # hold_ai must not grow a counter of its own.
    assert hold_ai.ai_service.quota_status is ai.quota_status
    assert "def quota_status" not in inspect.getsource(hold_ai)


# --- routing ------------------------------------------------------------------


def test_the_hold_snapshot_never_calls_the_exchange():
    """What makes `/analysis/chen` public at all.

    The snapshot wants two years of bars and eleven years of dividend reports.
    Fetched lazily, one anonymous visitor to a cold stock would queue tens of
    exchange calls on the limiter the realtime poll shares -- and capping it
    with the anonymous window instead would refuse every signed-out reader,
    because the window it reads is wider than ANONYMOUS_MAX_MONTHS.
    """
    import inspect

    from app.routers import analysis

    source = inspect.getsource(analysis._hold_snapshot)
    # The cache-only readers...
    assert "read_prices" in source
    assert "read_events" in source
    # ...and not the backfilling ones.
    assert "get_history" not in source
    assert "get_dividends" not in source


def test_the_free_checklist_route_is_not_gated_on_a_sign_in():
    """A card on a page anyone can open must answer for anyone who opens it."""
    import inspect

    from app.routers import analysis

    signature = inspect.signature(analysis.get_chen_analysis)
    assert "user" not in signature.parameters
    assert "limit_anonymous_window" not in inspect.getsource(analysis.get_chen_analysis)
