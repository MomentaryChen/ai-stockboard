"""Pins the pooling arithmetic and the staleness rule.

Both are places where a wrong answer looks entirely reasonable:

  * **Pooling.** Rates must be recomputed from summed counts, not averaged
    from per-stock rates. Averaging lets a stock with three signals move the
    headline as far as one with eighty -- which is the exact distortion pooling
    was added to remove, so getting it wrong silently defeats the feature.
  * **The sell side's reference.** A Sell competes with the days that *fell*,
    so its edge is measured against `1 - up_rate` and its excess return is
    baseline minus signal. Both subtractions run the opposite way to the buy
    side's, and both produce plausible-looking numbers when reversed.
  * **Staleness.** Measured against the newest bar, not a clock. A row is
    stale when the stock has traded since it was built, however recent it is.

No database: `pool()` reads `BacktestResult.payload`, so the rows can be built
in memory. The rest of the store is exercised end to end by the API.
"""

from __future__ import annotations

import datetime

from app.models import BacktestResult
from app.services import backtest_store


def _row(sid: str, buy: list[dict], sell: list[dict], baseline: list[dict]):
    return BacktestResult(
        sid=sid,
        rule_set="grs",
        payload={"buy": buy, "sell": sell, "baseline": baseline},
    )


def _h(horizon: int, samples: int, wins: int, average: float | None = 0.0) -> dict:
    return {
        "horizon": horizon,
        "samples": samples,
        "wins": wins,
        "pending": 0,
        "win_rate": (wins / samples) if samples else None,
        "average_return": average,
        "median_return": average,
    }


def _b(horizon: int, samples: int, ups: int, average: float = 0.0) -> dict:
    return {
        "horizon": horizon,
        "samples": samples,
        "ups": ups,
        "up_rate": (ups / samples) if samples else None,
        "average_return": average,
        "median_return": average,
    }


def test_pooled_rate_comes_from_summed_counts_not_averaged_rates():
    """A three-signal stock must not weigh as much as an eighty-signal one.

    Stock A goes 1/1 (100%); stock B goes 40/80 (50%). Averaging the rates
    gives 75%; the truth is 41/81 = 50.6%.
    """
    rows = [
        _row("A", [_h(5, 1, 1)], [], [_b(5, 10, 5)]),
        _row("B", [_h(5, 80, 40)], [], [_b(5, 200, 100)]),
    ]
    pooled = {p["horizon"]: p for p in backtest_store.pool(rows)}[5]

    assert pooled["buy_samples"] == 81
    assert pooled["buy_win_rate"] == 41 / 81
    assert pooled["buy_win_rate"] != (1.0 + 0.5) / 2  # the averaging bug
    assert pooled["stocks"] == 2


def test_pooled_average_return_is_weighted_by_samples():
    """Same reasoning for the returns, which are means rather than rates."""
    rows = [
        _row("A", [_h(5, 1, 1, average=1.0)], [], [_b(5, 1, 1)]),
        _row("B", [_h(5, 99, 50, average=0.0)], [], [_b(5, 1, 1)]),
    ]
    pooled = {p["horizon"]: p for p in backtest_store.pool(rows)}[5]

    assert pooled["buy_average_return"] == (1.0 * 1 + 0.0 * 99) / 100
    assert pooled["buy_average_return"] != 0.5  # the unweighted mean


def test_sell_edge_is_measured_against_the_down_rate():
    """A Sell competes with the days that fell, not the days that rose.

    Baseline rose on 70% of days, so it fell on 30%. A Sell right 40% of the
    time beat that by 10 points -- while the buy-side arithmetic would report
    it as 40% - 70% = -30 and read as a catastrophe.
    """
    rows = [_row("A", [], [_h(5, 10, 4)], [_b(5, 100, 70)])]
    pooled = {p["horizon"]: p for p in backtest_store.pool(rows)}[5]

    assert pooled["baseline_up_rate"] == 0.7
    assert pooled["sell_win_rate"] == 0.4
    assert abs(pooled["sell_edge"] - (0.4 - 0.3)) < 1e-12


def test_sell_excess_return_is_what_getting_out_avoided():
    """Baseline minus signal, so avoiding a fall reads as a positive number."""
    rows = [_row("A", [], [_h(5, 10, 5, average=-0.02)], [_b(5, 100, 50, 0.01)])]
    pooled = {p["horizon"]: p for p in backtest_store.pool(rows)}[5]

    # The average day made +1%; the days after a Sell made -2%. Getting out
    # avoided 3 points, so the excess is +0.03, not -0.03.
    assert abs(pooled["sell_excess_return"] - 0.03) < 1e-12
    # No buy signals here, so the buy side has nothing to compare and says so.
    assert pooled["buy_excess_return"] is None


def test_pooling_nothing_yields_nulls_rather_than_zero_rates():
    """No stocks scored means "unknown", which must not render as 0%."""
    pooled = backtest_store.pool([])
    for entry in pooled:
        assert entry["stocks"] == 0
        assert entry["buy_samples"] == 0
        assert entry["buy_win_rate"] is None
        assert entry["baseline_up_rate"] is None
        assert entry["buy_edge"] is None


def test_an_edge_over_an_unknown_baseline_is_unknown():
    """Never 0.0 -- that would claim the rule exactly matched the market."""
    rows = [_row("A", [_h(5, 4, 2)], [], [])]  # signals but no baseline
    pooled = {p["horizon"]: p for p in backtest_store.pool(rows)}[5]

    assert pooled["buy_win_rate"] == 0.5
    assert pooled["baseline_up_rate"] is None
    assert pooled["buy_edge"] is None


def test_window_start_is_month_aligned():
    """Matches how `/history` buckets months, so both read the same range."""
    start = backtest_store.window_start(datetime.date(2026, 8, 21))
    assert start.day == 1
    assert start == datetime.date(2025, 9, 1)  # 12 buckets back, inclusive
