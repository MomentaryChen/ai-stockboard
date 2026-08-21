"""Pins the deep technical lane, without a network call.

The generated prose cannot be asserted on. What can, and where the damage
would be:

  * a missing figure must never reach the model looking like a zero. The whole
    point of this lane is the extra data, and "foreign accounts net zero" read
    off an uncovered stock is a fabricated finding that looks exactly like a
    real one;
  * institutional nets must be scaled by turnover before anyone reasons about
    them, because a raw share count is not comparable between two stocks and
    the model will happily compare them anyway;
  * quick and deep must not evict each other from the cache. They are two
    answers about the same trading day, and the unique key is the only thing
    keeping both;
  * a forced regeneration must supersede its own depth and no other. Scoped
    wrongly, pressing 重新評估 on the deep panel deletes the quick verdict;
  * the deep read must never call the exchange. This button is on every
    watchlist row, and `ensure_dates` is bounded per request but not per page.
"""

from __future__ import annotations

import datetime
import inspect
import json
from types import SimpleNamespace

import pytest

from app.models import ChipDay, DailyPrice, FundamentalsAnnual
from app.schemas import BestFourPointResult
from app.services import chip as chip_service
from app.services.analysis import (
    ai,
    deep_features,
    deep_prompts,
    features,
    gemini,
    prompts,
)

SID = "2330"
AS_OF = datetime.date(2024, 2, 10)
START = datetime.date(2024, 1, 2)


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch):
    """The shared limiter would sleep 60s once a test makes its sixth call."""
    monkeypatch.setattr(gemini.gemini_throttle, "acquire", lambda: None)


# --- builders -----------------------------------------------------------------


def _prices(n: int = 40, capacity: int = 10_000) -> list[DailyPrice]:
    return [
        DailyPrice(
            sid=SID,
            date=START + datetime.timedelta(days=i),
            open=50.0,
            close=50.0 + (i % 5),
            high=56.0,
            low=48.0,
            capacity=capacity,
        )
        for i in range(n)
    ]


def _chips(
    days: int,
    *,
    foreign: int | None = 1_000,
    margin: int | None = 5_000,
    end: datetime.date | None = None,
) -> list[ChipDay]:
    """`days` consecutive sessions ending at `end`, newest last."""
    last = end or (START + datetime.timedelta(days=days - 1))
    return [
        ChipDay(
            sid=SID,
            date=last - datetime.timedelta(days=i),
            foreign_net=foreign,
            trust_net=None,
            dealer_net=None,
            total_net=foreign,
            margin_balance=margin,
            margin_change=10 if margin is not None else None,
            short_balance=None,
            short_change=None,
        )
        for i in range(days)
    ]


def _annual(years: list[int], *, eps: float | None = 5.0) -> list[FundamentalsAnnual]:
    return [
        FundamentalsAnnual(sid=SID, year=y, eps=eps, roe=15.0, net_income=None, equity=None)
        for y in years
    ]


def _features():
    extracted = features.extract(_prices())
    assert extracted is not None
    return extracted


def _traditional() -> BestFourPointResult:
    return BestFourPointResult(
        signal="hold", label="Don't touch", reasons=["四大買點條件皆不符合"]
    )


def _deep(**kw):
    params = dict(
        as_of=AS_OF,
        latest_close=100.0,
        prices=_prices(),
        chips=_chips(20),
        fundamentals=_annual(list(range(2016, 2024))),
    )
    params.update(kw)
    return deep_features.extract(**params)


# --- what is missing stays missing --------------------------------------------


def test_an_uncovered_stock_reports_gaps_rather_than_zeroes():
    """The failure this lane exists to avoid, stated as a test.

    A net of None and a net of 0 mean opposite things, and only one of them is
    a finding a model may cite.
    """
    result = _deep(chips=[], fundamentals=[])

    assert result.chip.days_covered == 0
    assert result.chip.foreign.net_5d_shares is None
    assert result.chip.foreign.net_20d_shares is None
    assert result.chip.foreign.streak == "none"
    assert "no_chip_data" in result.coverage_gaps
    assert "no_annual_fundamentals" in result.coverage_gaps


def test_partial_coverage_is_a_different_answer_from_none():
    """Four sessions is not "no data", and a 5-day net drawn from it is not a
    5-day net. The operator's action differs, so the slug does too."""
    thin = _deep(chips=_chips(3))
    full = _deep(chips=_chips(20))

    assert "partial_chip_coverage" in thin.coverage_gaps
    assert "no_chip_data" not in thin.coverage_gaps
    assert not {"partial_chip_coverage", "no_chip_data"} & set(full.coverage_gaps)


def test_margin_missing_while_institutions_are_covered_is_its_own_gap():
    """T86 landing before MI_MARGN is a normal state, not an absence of chip."""
    result = _deep(chips=_chips(20, margin=None))

    assert result.chip.days_covered == 20
    assert result.chip.margin_balance is None
    assert "no_margin_data" in result.coverage_gaps
    assert "no_chip_data" not in result.coverage_gaps


def test_a_thin_earnings_record_is_not_the_same_as_no_record():
    result = _deep(fundamentals=_annual([2023]))

    assert "no_annual_fundamentals" not in result.coverage_gaps
    assert "short_eps_history" in result.coverage_gaps


def test_a_loss_making_year_leaves_the_pe_unreported_rather_than_negative():
    """A negative trailing PE is a number that reads as cheap. It is not."""
    result = _deep(fundamentals=_annual(list(range(2016, 2024)), eps=-2.0))

    assert result.fundamentals.trailing_pe is None
    assert "no_trailing_pe" in result.coverage_gaps


# --- scaling ------------------------------------------------------------------


def test_institutional_flow_is_reported_as_a_share_of_turnover():
    """A raw share count is not comparable between two stocks; a percentage is."""
    result = _deep(chips=_chips(20, foreign=1_000), prices=_prices(capacity=10_000))

    # Five sessions at 1 000 shares bought against 10 000 traded each.
    assert result.chip.foreign.net_5d_shares == 5_000
    assert result.chip.foreign.net_5d_pct_of_volume == 10.0


def test_the_turnover_denominator_follows_the_chip_dates_not_the_newest_bars():
    """A stock whose chip coverage stops early must not be measured against
    volume from sessions its nets were never accumulated over -- that reads a
    real position as a negligible one."""
    prices = [
        DailyPrice(
            sid=SID,
            date=START + datetime.timedelta(days=i),
            open=50.0,
            close=50.0,
            high=51.0,
            low=49.0,
            # The sessions the chip covers traded a ninth of the later ones.
            capacity=1_000 if i < 5 else 9_000,
        )
        for i in range(10)
    ]
    chips = _chips(5, foreign=100, end=START + datetime.timedelta(days=4))

    result = _deep(prices=prices, chips=chips)

    # 500 shares against the 5 000 actually traded on those days -- not the
    # 45 000 traded on the five newest bars, which would read as 1.11%.
    assert result.chip.foreign.net_5d_shares == 500
    assert result.chip.foreign.net_5d_pct_of_volume == 10.0


def test_a_suspended_stock_has_no_percentage_rather_than_a_zero_one():
    result = _deep(chips=_chips(20), prices=_prices(capacity=0))

    assert result.chip.foreign.net_5d_shares == 5_000
    assert result.chip.foreign.net_5d_pct_of_volume is None


def test_a_balance_survives_a_half_filled_newest_session():
    """`chip_day` is written by two independent reports, so the newest row can
    carry nets and no balance. Reading only that row would drop the balance."""
    rows = _chips(5, margin=7_000)
    rows[-1].margin_balance = None  # newest session: nets in, margin not yet

    result = _deep(chips=rows)

    assert result.chip.margin_balance == 7_000


# --- the prompt ---------------------------------------------------------------


def test_both_depths_ask_the_same_question_with_the_same_rubric():
    """One definition of "large", or the two lanes are not comparable at all."""
    quick = prompts.system_instruction("zh-TW")
    deep = deep_prompts.system_instruction("zh-TW")

    assert prompts.ANSWER_SHAPE in quick
    assert prompts.ANSWER_SHAPE in deep
    assert deep_prompts.WireVerdict is prompts.WireVerdict


def test_the_deep_prompt_still_forbids_knowledge_the_model_was_not_given():
    """Half the prohibition is lifted here, which makes the rest load-bearing."""
    text = deep_prompts.system_instruction("zh-TW")

    assert "Do not use them" in text
    assert "You do not have news" in text


def test_the_deep_prompt_still_licenses_hold():
    """The sentence whose deletion recreates the twstock defect, in both lanes."""
    text = deep_prompts.system_instruction("zh-TW")

    assert '"hold" is a correct and expected answer' in text
    assert "Do not manufacture a trade" in text


def test_the_deep_prompt_refuses_a_raw_share_count_as_a_reason():
    text = deep_prompts.system_instruction("zh-TW")

    assert "share of turnover" in text
    assert "number without a scale" in text


def test_the_deep_prompt_caps_confidence_against_what_is_missing():
    text = deep_prompts.system_instruction("zh-TW")

    assert "coverage_gaps" in text
    assert 'caps your confidence at "medium"' in text


def test_the_user_turn_carries_the_chip_the_annuals_and_the_rule_verdict():
    prompt = deep_prompts.user_prompt(
        sid=SID,
        name="台積電",
        features=_features(),
        deep=_deep(),
        traditional=_traditional(),
    )

    assert SID in prompt and "台積電" in prompt
    assert "Institutional flow and margin" in prompt
    assert "Annual fundamentals" in prompt
    assert "net_5d_pct_of_volume" in prompt
    # The comparison the model is asked to agree or disagree with.
    assert "四大買賣點" in prompt and "Don't touch" in prompt


def test_the_gaps_reach_the_model_outside_the_json_as_well():
    """Rule 3 turns on this list; buried in a nested object it gets skipped."""
    prompt = deep_prompts.user_prompt(
        sid=SID,
        name="台積電",
        features=_features(),
        deep=_deep(chips=[], fundamentals=[]),
        traditional=_traditional(),
    )

    assert "Coverage gaps: no_chip_data, no_annual_fundamentals" in prompt


def test_a_fully_covered_stock_says_so_rather_than_leaving_the_line_blank():
    prompt = deep_prompts.user_prompt(
        sid=SID,
        name="台積電",
        features=_features(),
        deep=_deep(),
        traditional=_traditional(),
    )

    assert "Coverage gaps: (none" in prompt


def test_prompt_versions_cannot_collide_between_depths():
    """They version independently: editing the deep rules must not invalidate
    every quick verdict in the table."""
    assert deep_prompts.PROMPT_VERSION != prompts.PROMPT_VERSION
    assert ai.prompt_version("quick") == prompts.PROMPT_VERSION
    assert ai.prompt_version("deep") == deep_prompts.PROMPT_VERSION


# --- transport ----------------------------------------------------------------


_VALID = {
    "action": "enter",
    "size": "medium",
    "confidence": "medium",
    "headline": "外資連六買且站上季線。",
    "reasons": ["外資五日買超佔成交量 3.1%"],
    "risks": ["融資餘額同步增加"],
}


def _fake_client(payload: dict):
    calls = {"n": 0, "config": None, "contents": None}

    def generate_content(*, model, contents, config):  # noqa: ARG001
        calls["n"] += 1
        calls["config"] = config
        calls["contents"] = contents
        return SimpleNamespace(text=json.dumps(payload), usage_metadata=None)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


def test_generate_deep_sends_the_deep_instruction_and_its_own_ceiling(monkeypatch):
    """A deep answer cites more numbers, and one truncated mid-JSON is a failed
    request rather than a shorter one."""
    client, calls = _fake_client(_VALID)
    monkeypatch.setattr(gemini, "_client", lambda: client)

    result = gemini.generate_deep(
        sid=SID,
        name="台積電",
        features=_features(),
        deep=_deep(),
        traditional=_traditional(),
        model="gemini-2.5-flash",
    )

    assert result.verdict.action == "enter"
    assert "You do not have news" in calls["config"].system_instruction
    assert "Institutional flow and margin" in calls["contents"]
    assert (
        calls["config"].max_output_tokens
        == gemini._settings.gemini_deep_max_output_tokens
    )


def test_the_quick_lane_keeps_its_own_ceiling(monkeypatch):
    """The shared transport must not have quietly widened the cheap call."""
    client, calls = _fake_client(_VALID)
    monkeypatch.setattr(gemini, "_client", lambda: client)

    gemini.generate(
        sid=SID,
        name="台積電",
        features=_features(),
        traditional=_traditional(),
        model="gemini-2.5-flash",
    )

    assert calls["config"].max_output_tokens == gemini._settings.gemini_max_output_tokens


def test_both_depths_share_one_limiter_and_one_transport():
    """Two windows would let the lanes together spend twice what either may."""
    source = inspect.getsource(gemini.generate_deep)

    assert "generate_verdict(" in source
    # The retry loop lives in exactly one place.
    assert "for attempt in range(MAX_ATTEMPTS)" not in source


# --- cache and cost -----------------------------------------------------------


def test_the_cache_key_separates_the_two_depths():
    """Two answers about the same trading day; both are worth keeping."""
    source = inspect.getsource(ai._find)

    assert "AiAnalysis.depth == depth" in source
    assert "prompt_version(depth)" in source


def test_a_forced_regeneration_supersedes_only_its_own_depth():
    """Scoped wrongly, 重新評估 on the deep panel deletes the quick verdict."""
    source = inspect.getsource(ai.get_or_create)
    delete = source[source.index("__table__.delete()") :]

    assert "AiAnalysis.depth == depth" in delete
    assert "prompts.PROMPT_VERSION" not in delete


def test_the_deep_lane_does_not_get_a_table_of_its_own():
    """Which is what makes the shared daily allowance automatic rather than a
    second counter somebody has to remember to add."""
    source = inspect.getsource(ai.get_or_create)

    assert source.count("row = AiAnalysis(") == 1
    assert "AiHoldAnalysis(" not in source


def test_the_deep_read_never_calls_the_exchange():
    """The button is on every watchlist row. `ensure_dates` is bounded per
    request but not per page: twenty rows would queue tens of exchange calls
    on the limiter the realtime poll shares."""
    source = inspect.getsource(ai._deep_inputs)

    # The cache-only reader...
    assert "read_recent" in source
    # ...and not the fetching one.
    assert "get_chips" not in source
    assert "ensure_dates" not in source


def test_the_cache_only_chip_reader_really_does_not_fetch(monkeypatch):
    """The source check above pins the call site; this pins the callee."""

    def _explode(*args, **kwargs):
        raise AssertionError("read_recent must never reach the exchange")

    monkeypatch.setattr(chip_service, "ensure_dates", _explode)
    monkeypatch.setattr(chip_service, "trading_dates", lambda db, sid, days: [])

    assert chip_service.read_recent(None, SID, 20) == []


def test_the_stored_row_records_the_deep_inputs_not_just_the_price_series():
    """Otherwise a bad deep verdict is unexplainable after the fact: the row
    would claim the model saw only a chart."""
    source = inspect.getsource(ai.get_or_create)

    assert '"deep": deep.model_dump(mode="json")' in source
