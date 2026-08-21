"""Pins the deep 存股 lane, without a network call.

The generated prose cannot be asserted on. What can, and where the damage
would be:

  * a missing figure must never reach the model looking like a zero. This lane
    exists for the extra data, and "paid nothing in 2019" read off a payout
    archive that was never fetched is a fabricated finding shaped exactly like
    a real one;
  * a payout ratio must be measured against the earnings it actually came out
    of. Taiwan distributes a year's profit the year after, so dividing this
    year's cash by this year's EPS answers a different question and answers it
    confidently;
  * a percentile drawn from a fortnight of sessions looks identical to one
    drawn from a decade. The band has to report its own length, and a short one
    has to be a coverage gap;
  * quick and deep must not evict each other from the cache -- they are two
    answers about the same company on the same day, and the unique key is the
    only thing keeping both;
  * a forced regeneration must supersede its own depth and no other;
  * the deep read must never call the exchange. It hangs off a snapshot that
    already reads two years of bars and eleven of dividend reports on a page
    anyone can open.
"""

from __future__ import annotations

import datetime
import inspect
import json
from types import SimpleNamespace

import pytest

from app.models import (
    AiHoldAnalysis,
    ChipDay,
    DailyPrice,
    DividendEvent,
    FundamentalsAnnual,
    ValuationDay,
)
from app.services import chip as chip_service
from app.services import valuation as valuation_service
from app.services.analysis import (
    hold_ai,
    hold_deep_features,
    hold_deep_prompts,
    hold_gemini,
    hold_prompts,
)

from tests.test_hold_ai import _features, _rules

SID = "2880"
AS_OF = datetime.date(2026, 3, 20)


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch):
    """The shared limiter would sleep 60s once a test makes its sixth call."""
    monkeypatch.setattr(hold_gemini.gemini_throttle, "acquire", lambda: None)


# --- builders -----------------------------------------------------------------


def _annual(years: range | list[int], *, eps: float = 1.5) -> list[FundamentalsAnnual]:
    return [
        FundamentalsAnnual(sid=SID, year=y, eps=eps, roe=11.0, net_income=None, equity=None)
        for y in years
    ]


def _dividends(years: range | list[int], *, cash: float = 1.0) -> list[DividendEvent]:
    return [
        DividendEvent(
            sid=SID,
            ex_date=datetime.date(y, 8, 15),
            cash_dividend=cash,
            stock_dividend=None,
        )
        for y in years
    ]


def _prices(n: int = 30) -> list[DailyPrice]:
    start = AS_OF - datetime.timedelta(days=n)
    return [
        DailyPrice(
            sid=SID,
            date=start + datetime.timedelta(days=i),
            open=20.0,
            close=20.0,
            high=21.0,
            low=19.0,
            capacity=10_000,
        )
        for i in range(n)
    ]


def _chips(days: int, *, foreign: int | None = 1_000) -> list[ChipDay]:
    last = AS_OF
    return [
        ChipDay(
            sid=SID,
            date=last - datetime.timedelta(days=i),
            foreign_net=foreign,
            trust_net=None,
            dealer_net=None,
            total_net=foreign,
            margin_balance=5_000,
            margin_change=10,
            short_balance=None,
            short_change=None,
        )
        for i in range(days)
    ]


def _valuations(days: int, *, pes: list[float] | None = None) -> list[ValuationDay]:
    """`days` sessions ending at AS_OF, newest last. `pes` is oldest first."""
    return [
        ValuationDay(
            sid=SID,
            date=AS_OF - datetime.timedelta(days=days - 1 - i),
            pe_ratio=(pes[i] if pes is not None else 12.0 + i * 0.01),
            pb_ratio=1.1,
            dividend_yield=5.0,
        )
        for i in range(days)
    ]


def _deep(**kw):
    params = dict(
        as_of=AS_OF,
        prices=_prices(),
        chips=_chips(20),
        fundamentals=_annual(range(2016, 2026)),
        dividends=_dividends(range(2017, 2026)),
        valuations=_valuations(120),
    )
    params.update(kw)
    return hold_deep_features.extract(**params)


# --- what is missing stays missing --------------------------------------------


def test_an_uncovered_company_reports_gaps_rather_than_zeroes():
    """A year absent from the archive and a year the company skipped are
    opposite findings, and only one of them is a reason a model may cite."""
    result = _deep(chips=[], fundamentals=[], dividends=[], valuations=[])

    assert result.fundamentals.years == []
    assert result.fundamentals.avg_payout_ratio_pct is None
    assert result.valuation.pe_percentile is None
    assert set(result.coverage_gaps) >= {
        "no_chip_data",
        "no_annual_fundamentals",
        "no_dividend_history",
        "no_valuation_history",
    }


def test_a_thin_earnings_record_is_not_the_same_as_no_record():
    result = _deep(fundamentals=_annual([2025]))

    assert "no_annual_fundamentals" not in result.coverage_gaps
    assert "short_eps_history" in result.coverage_gaps


def test_a_short_valuation_band_is_its_own_gap():
    """A fortnight of sessions produces a percentile that looks exactly like a
    decade's. The prompt caps its confidence on this slug and is told not to
    cite the percentile at all."""
    thin = _deep(valuations=_valuations(10))
    full = _deep(valuations=_valuations(120))

    assert "short_valuation_history" in thin.coverage_gaps
    assert "no_valuation_history" not in thin.coverage_gaps
    assert not {"short_valuation_history", "no_valuation_history"} & set(
        full.coverage_gaps
    )
    # The figure itself still travels: it is the percentile that is unsafe.
    assert thin.valuation.pe_ratio is not None
    assert thin.valuation.days_covered == 10


# --- the payout ratio ---------------------------------------------------------


def test_a_payout_is_measured_against_the_year_it_was_earned_in():
    """Taiwan distributes a fiscal year's profit the year after. Dividing this
    year's cash by this year's EPS answers a different question."""
    result = _deep(
        fundamentals=[
            FundamentalsAnnual(sid=SID, year=2024, eps=2.0, roe=11.0),
            FundamentalsAnnual(sid=SID, year=2025, eps=4.0, roe=11.0),
        ],
        dividends=_dividends([2025], cash=1.0),
    )

    year = next(y for y in result.fundamentals.years if y.year == 2025)
    # 1.00 against 2024's 2.00 EPS, not against 2025's 4.00.
    assert year.payout_ratio_pct == 50.0


def test_a_payout_out_of_a_loss_is_undefined_rather_than_negative():
    """-180% reads as a number. What it means is "paid a dividend it did not
    earn", which belongs in the risks the model writes, not in a ratio."""
    result = _deep(
        fundamentals=[FundamentalsAnnual(sid=SID, year=2024, eps=-1.0, roe=None)],
        dividends=_dividends([2025], cash=1.0),
    )

    year = next(y for y in result.fundamentals.years if y.year == 2025)
    assert year.payout_ratio_pct is None


def test_the_window_payout_is_summed_rather_than_averaged():
    """A quarterly payer's distribution of one fiscal year lands in two calendar
    ones, so each yearly ratio is a little wrong in a direction that cancels
    inside a sum. Averaging the ratios keeps the error."""
    result = _deep(
        fundamentals=[
            FundamentalsAnnual(sid=SID, year=2023, eps=1.0, roe=None),
            FundamentalsAnnual(sid=SID, year=2024, eps=3.0, roe=None),
        ],
        dividends=[
            DividendEvent(sid=SID, ex_date=datetime.date(2024, 8, 1), cash_dividend=1.0),
            DividendEvent(sid=SID, ex_date=datetime.date(2025, 8, 1), cash_dividend=1.0),
        ],
    )

    # Sum: (1.0 + 1.0) / (1.0 + 3.0) = 50%. The mean of the two yearly ratios
    # (100% and 33.3%) would be 66.7%.
    assert result.fundamentals.avg_payout_ratio_pct == 50.0


def test_a_payout_after_the_snapshot_date_is_not_counted():
    """A snapshot replayed for a past date must not see a payout that had not
    happened yet -- the same bound the checklist applies."""
    result = _deep(
        as_of=datetime.date(2026, 3, 20),
        dividends=[
            DividendEvent(sid=SID, ex_date=datetime.date(2026, 8, 15), cash_dividend=1.0)
        ],
    )

    assert all(y.year != 2026 or y.cash_dividend is None for y in result.fundamentals.years)


# --- the trend ----------------------------------------------------------------


def test_a_growth_rate_is_withheld_from_a_loss_making_end_but_the_direction_is_not():
    """A rate compounded off a negative base is arithmetic the code will happily
    produce and nobody can interpret. `eps_down_years` is defined either way,
    which is why both are reported."""
    result = _deep(
        fundamentals=[
            FundamentalsAnnual(sid=SID, year=2023, eps=-1.0, roe=None),
            FundamentalsAnnual(sid=SID, year=2024, eps=2.0, roe=None),
            FundamentalsAnnual(sid=SID, year=2025, eps=1.0, roe=None),
        ],
        dividends=[],
    )

    assert result.fundamentals.eps_cagr_pct is None
    assert result.fundamentals.eps_down_years == 1


def test_a_decade_of_growth_is_reported_as_a_rate():
    result = _deep(
        fundamentals=[
            FundamentalsAnnual(sid=SID, year=2021, eps=1.0, roe=None),
            FundamentalsAnnual(sid=SID, year=2025, eps=2.0, roe=None),
        ],
        dividends=[],
    )

    # 1.00 to 2.00 over four years.
    assert result.fundamentals.eps_cagr_pct == pytest.approx(18.92, abs=0.01)
    # Zero here would read as "earnings never fell" off two figures four years
    # apart. Nothing was compared, so nothing is claimed.
    assert result.fundamentals.eps_down_years is None


def test_the_series_stops_at_the_checklists_own_window():
    """Ten years, so both lanes are talking about the same decade."""
    result = _deep(
        fundamentals=_annual(range(2000, 2026)),
        dividends=_dividends(range(2000, 2026)),
    )

    assert len(result.fundamentals.years) == hold_deep_features.WINDOW_YEARS
    # 2026 has no record yet in March, so the window ends at 2025 rather than
    # spending its newest slot on an empty year -- the treatment the payout
    # streak rules already give the current year.
    assert result.fundamentals.years[0].year == 2025
    assert result.fundamentals.years[-1].year == 2016


# --- the band -----------------------------------------------------------------


def test_the_percentile_places_today_inside_the_stocks_own_range():
    """Cheap against a sector ceiling and cheap against its own history are two
    different claims; this is the second one."""
    # Ninety sessions climbing from 10 to 19, so the newest is the highest.
    result = _deep(valuations=_valuations(90, pes=[10.0 + i * 0.1 for i in range(90)]))

    assert result.valuation.pe_percentile == pytest.approx(98.9, abs=0.2)


def test_a_flat_figure_does_not_read_as_expensive_against_itself():
    """Counting at-or-below would put a PE that has not moved in a quarter at
    the top of its own range."""
    result = _deep(valuations=_valuations(90, pes=[12.0] * 90))

    assert result.valuation.pe_percentile == 0.0


def test_a_blank_newest_session_does_not_erase_the_band():
    """The exchange leaves PE empty for a company with no positive trailing
    earnings; one such session is not the end of the history."""
    rows = _valuations(90)
    rows[-1].pe_ratio = None  # newest session

    result = _deep(valuations=rows)

    assert result.valuation.pe_ratio is not None
    assert result.valuation.days_covered == 90


# --- the prompt ---------------------------------------------------------------


def test_both_depths_answer_in_the_same_shape():
    """One definition of "strong", or the two assessments are not comparable."""
    assert hold_deep_prompts.WireVerdict is hold_prompts.WireVerdict
    assert hold_deep_prompts.to_verdict is hold_prompts.to_verdict


def test_the_deep_prompt_still_forbids_knowledge_the_model_was_not_given():
    """The prohibition that matters most here, at exactly the moment it becomes
    tempting: the model has just been handed eight real EPS figures."""
    text = hold_deep_prompts.system_instruction("zh-TW")

    assert "Do not use them" in text
    assert "You do not have news" in text


def test_the_deep_prompt_forbids_explaining_the_series_it_can_only_see():
    """A model handed a decade of EPS will narrate a cause for it. The cause is
    the one thing not in the data."""
    text = hold_deep_prompts.system_instruction("zh-TW")

    assert "You may not" in text and "explain *why* it moved" in text


def test_the_deep_prompt_states_which_way_each_percentile_reads():
    """Low PE percentile is cheap; low yield percentile is the opposite.
    Getting this backwards inverts the verdict."""
    text = hold_deep_prompts.system_instruction("zh-TW")

    assert "dividend_yield_percentile` means the opposite" in text
    assert "short_valuation_history" in text


def test_the_deep_prompt_caps_confidence_against_what_is_missing():
    text = hold_deep_prompts.system_instruction("zh-TW")

    assert "coverage_gaps" in text
    # Line-wrapped in the instruction, so the assertion cannot span the break.
    assert 'confidence at "medium"' in text


def test_the_user_turn_carries_the_record_the_band_the_flow_and_the_checklist():
    prompt = hold_deep_prompts.user_prompt(
        features=_features(), deep=_deep(), rules=_rules()
    )

    assert "Year-by-year earnings and payout record" in prompt
    assert "Valuation against this stock's own stored history" in prompt
    assert "Institutional flow and margin" in prompt
    assert "payout_ratio_pct" in prompt
    assert "陳重銘存股檢查表" in prompt


def test_the_gaps_reach_the_model_outside_the_json_as_well():
    """Two rules turn on that list; buried in a nested object it gets skipped."""
    prompt = hold_deep_prompts.user_prompt(
        features=_features(),
        deep=_deep(chips=[], fundamentals=[], dividends=[], valuations=[]),
        rules=_rules(),
    )

    assert "Coverage gaps: no_chip_data" in prompt
    assert "no_valuation_history" in prompt


def test_a_fully_covered_company_says_so_rather_than_leaving_the_line_blank():
    prompt = hold_deep_prompts.user_prompt(
        features=_features(), deep=_deep(), rules=_rules()
    )

    assert "Coverage gaps: (none" in prompt


def test_prompt_versions_cannot_collide_between_depths_or_lanes():
    """They version independently: editing the deep rules must not invalidate
    every quick verdict in the table. The `hold-` prefix keeps both away from
    the technical lane's versions in an evaluation that reads both tables."""
    assert hold_deep_prompts.PROMPT_VERSION != hold_prompts.PROMPT_VERSION
    assert hold_deep_prompts.PROMPT_VERSION.startswith("hold-")
    assert hold_ai.prompt_version("quick") == hold_prompts.PROMPT_VERSION
    assert hold_ai.prompt_version("deep") == hold_deep_prompts.PROMPT_VERSION


# --- transport ----------------------------------------------------------------


_VALID = {
    "suitability": "ok",
    "confidence": "medium",
    "headline": "近十年年年配息，配發率約佔前一年度 EPS 的 67%。",
    "reasons": ["近十年配息合計約為同期 EPS 的 67%"],
    "risks": ["本益比位於自身區間高位"],
    "agrees_with_rules": True,
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
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)

    result = hold_gemini.generate_deep(
        features=_features(),
        deep=_deep(),
        rules=_rules(),
        model="gemini-2.5-flash",
    )

    assert result.verdict.suitability == "ok"
    assert "explain *why* it moved" in calls["config"].system_instruction
    assert "Year-by-year earnings and payout record" in calls["contents"]
    assert (
        calls["config"].max_output_tokens
        == hold_gemini._settings.gemini_deep_max_output_tokens
    )


def test_the_quick_hold_lane_keeps_its_own_ceiling(monkeypatch):
    """The shared transport must not have quietly widened the cheap call."""
    client, calls = _fake_client(_VALID)
    monkeypatch.setattr(hold_gemini, "_client", lambda: client)

    hold_gemini.generate(features=_features(), rules=_rules(), model="gemini-2.5-flash")

    assert (
        calls["config"].max_output_tokens
        == hold_gemini._settings.gemini_max_output_tokens
    )


def test_both_hold_depths_share_one_limiter_and_one_transport():
    """Two windows would let the depths together spend twice what either may."""
    source = inspect.getsource(hold_gemini.generate_deep)

    assert "generate_hold_verdict(" in source
    assert "for attempt in range(MAX_ATTEMPTS)" not in source


# --- cache and cost -----------------------------------------------------------


def test_the_cache_key_separates_the_two_depths():
    """Two answers about the same company on the same day; both worth keeping."""
    source = inspect.getsource(hold_ai._find)

    assert "AiHoldAnalysis.depth == depth" in source
    assert "prompt_version(depth)" in source


def test_a_forced_regeneration_supersedes_only_its_own_depth():
    """Scoped wrongly, 重新深度評估 deletes the checklist verdict beside it."""
    source = inspect.getsource(hold_ai.get_or_create)
    delete = source[source.index("__table__.delete()") :]

    assert "AiHoldAnalysis.depth == depth" in delete
    assert "hold_prompts.PROMPT_VERSION" not in delete


def test_the_deep_lane_does_not_get_a_table_of_its_own():
    """Which is what keeps the one shared daily allowance automatic rather than
    a third counter somebody has to remember to add."""
    source = inspect.getsource(hold_ai.get_or_create)

    assert source.count("row = AiHoldAnalysis(") == 1
    assert "AiAnalysis(" not in source


def test_the_deep_read_never_calls_the_exchange():
    """It hangs off a snapshot that already reads two years of bars and eleven
    of dividend reports, on a page anyone can open."""
    source = inspect.getsource(hold_ai.deep_inputs)

    # The cache-only readers...
    assert "chip_service.read_recent" in source
    assert "valuation_service.read_recent" in source
    # ...and not the fetching ones.
    assert "get_chips" not in source
    assert "ensure_dates" not in source


def test_the_cache_only_valuation_reader_really_does_not_fetch(monkeypatch):
    """The source check above pins the call site; this pins the callee."""

    def _explode(*_args, **_kwargs):
        raise AssertionError("read_recent must never reach the exchange")

    monkeypatch.setattr(valuation_service, "refresh", _explode, raising=False)
    monkeypatch.setattr(chip_service, "ensure_dates", _explode)

    source = inspect.getsource(valuation_service.read_recent)
    assert "_fetch" not in source and "requests" not in source


def test_the_stored_row_records_the_deep_inputs_not_just_the_snapshot():
    """Otherwise a bad deep verdict is unexplainable after the fact: the row
    would claim the model saw only the checklist's aggregates."""
    source = inspect.getsource(hold_ai.get_or_create)

    assert '"deep": deep.model_dump(mode="json")' in source


# --- the free read ------------------------------------------------------------


def _row(depth: str, features: dict | None = None) -> AiHoldAnalysis:
    return AiHoldAnalysis(
        sid=SID,
        as_of=AS_OF,
        model="gemini-2.5-flash",
        prompt_version=hold_ai.prompt_version(depth),
        locale="zh-TW",
        depth=depth,
        suitability="ok",
        confidence="medium",
        headline="h",
        reasons=[],
        risks=[],
        agrees_with_rules=None,
        features=features or {},
    )


def test_the_free_read_serves_the_better_informed_verdict():
    """Depth is an input where it costs money and an output where it does not:
    nobody is charged for reading either, so withholding the deeper answer from
    someone who did not name it would be showing the worse one for no reason."""
    quick, deep = _row("quick"), _row("deep")

    assert hold_ai._best([quick, deep]) is deep
    assert hold_ai._best([deep, quick]) is deep
    assert hold_ai._best([quick]) is quick
    assert hold_ai._best([]) is None


def test_both_live_wordings_are_readable_for_free():
    """Filtering the free read on the quick version alone would make a deep
    verdict invisible to every reader who did not generate it."""
    assert set(hold_ai._live_prompt_versions()) == {
        hold_prompts.PROMPT_VERSION,
        hold_deep_prompts.PROMPT_VERSION,
    }


def test_a_deep_row_rehydrates_its_inputs_from_its_own_audit_copy():
    stored = {
        "snapshot": _features().model_dump(mode="json"),
        "deep": _deep().model_dump(mode="json"),
    }

    rehydrated = hold_ai._stored_deep(_row("deep", stored))

    assert rehydrated is not None
    assert rehydrated.coverage_gaps == _deep().coverage_gaps
    assert rehydrated.fundamentals.years[0].year == _deep().fundamentals.years[0].year


def test_a_quick_row_has_no_deep_block_to_rehydrate():
    """Which is what keeps the checklist-only disclaimer on a checklist verdict."""
    assert hold_ai._stored_deep(_row("quick", {"anything": 1})) is None


def test_an_unreadable_audit_copy_degrades_rather_than_failing_the_page():
    """A row written before this lane existed, or by a future shape. The reader
    was only trying to show a cached answer; a 500 is the wrong outcome."""
    assert hold_ai._stored_deep(_row("deep", {})) is None
    assert hold_ai._stored_deep(_row("deep", {"deep": {"chip": "nonsense"}})) is None
