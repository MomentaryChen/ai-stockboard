"""Pins the rules that decide what a reader is told.

These are the parts where a mistake would still produce a tidy, plausible price:
the trade replay, the choice of entry level, and whether future data leaks in.
"""

from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd

from market_engine import scoring, simulate, tracking
from market_engine.simulate import ROUND_TRIP_COST


def _flat(n=40, price=100.0):
    return np.full(n, price), np.full(n, price), np.full(n, price)


def test_limit_below_the_range_never_fills():
    o, l, c = _flat()
    assert simulate.sim_trade(o, l, c, 0, 0.02, 1.0, None, 10, 5) == (False, None, False)


def test_gap_below_the_limit_fills_at_the_open_not_the_limit():
    o, l, c = _flat()
    o[1], l[1] = 90.0, 90.0
    filled, net, _ = simulate.sim_trade(o, l, c, 0, 0.02, 1.0, None, 10, 5)
    assert filled is True
    assert math.isclose(net, 100.0 / 90.0 - 1 - ROUND_TRIP_COST)


def test_stop_touched_on_the_fill_day_counts_as_hit():
    """The bar cannot say which came first, so the worse order is assumed."""
    o, l, c = _flat()
    l[1] = 90.0
    filled, net, stopped = simulate.sim_trade(o, l, c, 0, 0.02, 0.0, 2.5, 10, 5)
    assert filled is True and stopped is True
    assert math.isclose(net, -0.05 - ROUND_TRIP_COST)


def test_unfinished_trade_is_not_counted():
    o, l, c = _flat(n=10)
    assert simulate.sim_trade(o, l, c, 0, 0.02, 0.0, None, 20, 5)[0] is None


def test_cost_is_the_stock_rate_not_the_etf_rate():
    assert math.isclose(ROUND_TRIP_COST, 0.001425 * 2 + 0.003)


def _level(k, ev=0.05, lo=0.01, fills=100):
    return {"k": k, "ev": ev, "ci": (lo, 0.1), "fills": fills, "fill_rate": 0.5, "worst": -0.2, "kelly": 2.0, "baseline_ev": 0.02}


def test_entry_is_the_highest_level_that_holds_up():
    assert simulate.choose_entry([_level(0), _level(0.5)])["k"] == 0


def test_lucky_level_with_a_losing_neighbour_is_rejected():
    grid = [_level(0, lo=-0.01), _level(0.5), _level(1.0, lo=-0.01), _level(1.5), _level(2.0)]
    assert simulate.choose_entry(grid)["k"] == 1.5


def test_too_few_fills_is_not_a_price():
    assert simulate.choose_entry([_level(0, lo=-0.01), _level(3.0, fills=29)]) is None


def test_no_level_means_no_recommendation():
    assert simulate.choose_entry([_level(0, ev=-0.01, lo=-0.05)]) is None


def test_weight_is_halved_without_verification():
    level = _level(0, lo=0.05)
    unverified = simulate.max_weight(level, 0.02, None, False, 0.01, 0.25, 0.10)
    verified = simulate.max_weight(level, 0.02, None, True, 0.01, 0.25, 0.10)
    assert math.isclose(verified, 0.05) and math.isclose(unverified, 0.025)


def test_hold_state():
    assert simulate.hold_state(0.05, 0.01, 0.1, 0.05) == "strong"
    assert simulate.hold_state(0.05, -0.01, 0.1, 0.05) == "unclear"
    assert simulate.hold_state(-0.01, -0.05, 0.03, 0.05) == "weak"
    assert simulate.hold_state(0.01, -0.05, 0.03, -0.01) == "weak"
    assert simulate.hold_state(None, None, None, None) == "unknown"


def _history(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    bars = pd.DataFrame(
        {"Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Adj Close": close},
        index=dates,
    )
    return bars, pd.Series(close, index=dates)


def test_returns_start_at_the_next_open():
    bars, market = _history(300)
    frame, _ = scoring.build_frame(bars, market)
    i = 100
    expected = bars["Adj Close"].iloc[i + 20] / bars["Open"].iloc[i + 1] - 1
    assert math.isclose(frame["fwd20"].iloc[i], expected)


def test_features_do_not_change_when_the_future_does():
    bars, market = _history(400)
    frame, _ = scoring.build_frame(bars, market)
    changed = bars.copy()
    changed.iloc[300:, :] = changed.iloc[300:, :] * 3
    frame2, _ = scoring.build_frame(changed, market)
    pd.testing.assert_frame_equal(frame[scoring.FEATURES].iloc[:300], frame2[scoring.FEATURES].iloc[:300])


def test_walk_forward_grade_ignores_later_prices():
    """Each grade may depend on prices up to its own date and nothing later."""
    bars, market = _history(1400)
    frame, _ = scoring.build_frame(bars, market)
    original = {r["date"]: r["state"] for r in scoring.hold_walk_forward(frame, 150)}
    changed = bars.copy()
    changed.iloc[-5:, :] = changed.iloc[-5:, :] * 2
    frame2, _ = scoring.build_frame(changed, market)
    again = {r["date"]: r["state"] for r in scoring.hold_walk_forward(frame2, 150)}
    shared = [d for d in original if d in again and d < bars.index[-200]]
    assert shared
    assert all(original[d] == again[d] for d in shared)


def test_clean_drops_invented_holiday_bars_and_zero_prices():
    bars, _ = _history(30)
    calendar = bars.index.delete(10)
    bars.iloc[5, bars.columns.get_loc("Open")] = 0.0
    cleaned = scoring.clean(bars, calendar, "9999", "上櫃")
    assert bars.index[10] not in cleaned.index
    assert (cleaned["Open"] > 0).all()


def _bars(close, start="2026-01-01"):
    close = np.asarray(close, float)
    index = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"o": close, "h": close * 1.001, "l": close * 0.999, "c": close}, index=index)


def _call_file(date, **overrides):
    call = {
        "sid": "1111",
        "yahoo": "1111.TW",
        "market": "上市",
        "sector": "水泥",
        "close": 100.0,
        "risk_line": 95.0,  # An ATR of 2%.
        "hold_state": "strong",
        "short_entry_k": 0,
        "long_entry_k": None,
        **overrides,
    }
    return {"as_of": str(date.date()), "engine_version": "1.0.0", "calls": [call]}


def test_only_matured_calls_are_graded():
    frame = _bars([100.0] * 30)
    rows = tracking.grade([_call_file(frame.index[0])], {"1111": frame}, frame)
    assert [(r["horizon"], r["call"]) for r in rows] == [("short", "buy_now")]


def test_grade_compares_with_the_benchmark_over_the_same_period():
    frame = _bars(np.linspace(100, 120, 40))
    flat = _bars([50.0] * 40)
    row = tracking.grade([_call_file(frame.index[0])], {"1111": frame}, flat)[0]
    assert row["benchmark_return"] == 0.0
    expected = frame["c"].iloc[21] / frame["o"].iloc[1] - 1 - simulate.ROUND_TRIP_COST
    assert math.isclose(row["market_entry_return"], expected)


def test_no_buy_still_records_what_buying_would_have_done():
    frame = _bars([100.0] * 70)
    row = next(r for r in tracking.grade([_call_file(frame.index[0])], {"1111": frame}, frame) if r["horizon"] == "long")
    assert row["call"] == "no_buy"
    assert row["followed_return"] is None and row["filled"] is False
    assert math.isclose(row["market_entry_return"], -simulate.ROUND_TRIP_COST)


def test_unfilled_dip_order_has_no_return_of_its_own():
    frame = _bars([100.0] * 30)
    row = tracking.grade([_call_file(frame.index[0], short_entry_k=2.0)], {"1111": frame}, frame)[0]
    assert row["call"] == "buy_dip" and row["filled"] is False and row["followed_return"] is None


def test_call_files_round_trip_and_expire(tmp_path):
    entry = {"entry": {"k_atr": 0}}
    payload = {
        "as_of": "2026-10-30",
        "engine_version": "1.0.0",
        "recommendations": [
            {"sid": "1111", "market": "上市", "sector": "水泥", "close": 100.0, "risk_line": 95.0, "hold_state": "weak",
             "status": "ok", "short": entry, "long": {"entry": None}},
            {"sid": "2222", "status": "failed"},
        ],
    }
    tracking.save_calls(str(tmp_path), payload, {"1111": "1111.TW"})
    loaded = tracking.load_calls(str(tmp_path), today=datetime.date(2026, 11, 1))
    assert [c["sid"] for c in loaded[0]["calls"]] == ["1111"]
    assert loaded[0]["calls"][0]["long_entry_k"] is None
    assert tracking.load_calls(str(tmp_path), today=datetime.date(2027, 6, 1)) == []
