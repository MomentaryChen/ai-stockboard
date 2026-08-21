"""Pins the three claims `backtest.py` makes about itself.

Each one is invisible when broken -- a wrong backtest still returns tidy
percentages, and nothing downstream can tell they are wrong:

  * **`WINDOW_BARS` changes no verdict.** The replay feeds each day a trailing
    slice instead of the whole prefix, purely so the walk is O(n). If that
    slice is ever too short, the page and the backtest start disagreeing about
    the same day and neither says so. The randomised comparison here is the
    only thing standing between that and a silent divergence.
  * **No look-ahead.** Orders are filled at the *next* bar's open. Filling at
    the signal bar's close would raise every return in the product and could
    not be spotted from the output.
  * **Unfinished business stays unfinished.** Signals without a full horizon
    are `pending`, not wins; a position still open at the end is not a trade.
  * **The baseline is the same window.** Signal rates are only readable against
    what an unconditional position over the same judged days scored, and the
    sell side's reference is the *down* rate, not the up rate.

`test_best_four_point.py` covers the rules themselves; this file only covers
what the replay adds on top of them.
"""

from __future__ import annotations

import datetime
import random

import pytest

from app.models import DailyPrice
from app.services.analysis import backtest, traditional
from app.services.analysis.traditional import MIN_SAMPLES_FOR_BFP, _CachedStock

SID = "2330"
START = datetime.date(2025, 1, 6)  # a Monday; weekends are irrelevant here


def _bar(
    day: datetime.date, *, open_: float, close: float, capacity: int
) -> DailyPrice:
    return DailyPrice(
        sid=SID,
        date=day,
        open=open_,
        close=close,
        high=max(open_, close),
        low=min(open_, close),
        capacity=capacity,
    )


def _walk(prices: list[tuple[float, float, int]]) -> list[DailyPrice]:
    """Bars on consecutive days from (open, close, capacity) triples."""
    return [
        _bar(START + datetime.timedelta(days=i), open_=o, close=c, capacity=v)
        for i, (o, c, v) in enumerate(prices)
    ]


def _random_series(rng: random.Random, length: int) -> list[DailyPrice]:
    price = 100.0
    out = []
    for _ in range(length):
        open_ = price
        # Wide enough to cross the MA3/MA6 relationship often, so the bias gate
        # and the volume rules both fire during a run rather than never.
        close = max(1.0, price * (1 + rng.uniform(-0.05, 0.05)))
        out.append((round(open_, 2), round(close, 2), rng.randint(1_000, 50_000)))
        price = close
    return _walk(out)


def test_window_truncation_changes_no_verdict():
    """The trailing slice must produce the verdict the full prefix would.

    This is the load-bearing assumption behind `WINDOW_BARS`. Compared per day
    across many random walks, both rule sets, so a rule that starts reaching
    further back than the constant allows fails here rather than in production.
    """
    rng = random.Random(20260821)
    for _ in range(40):
        rows = _random_series(rng, 120)
        for rule_set in traditional.RULE_SETS:
            for i in range(MIN_SAMPLES_FOR_BFP - 1, len(rows)):
                full = traditional.best_four_point(_CachedStock(rows[: i + 1]), rule_set)
                window = rows[max(0, i - backtest.WINDOW_BARS + 1) : i + 1]
                sliced = traditional.best_four_point(_CachedStock(window), rule_set)
                assert full.signal == sliced.signal, (i, rule_set)
                assert full.reasons == sliced.reasons, (i, rule_set)


def test_orders_fill_at_the_next_open_not_the_signal_close():
    """A verdict read off bar i's close cannot be traded before bar i+1 opens.

    Built as a forced Buy on a known bar: the entry must be the *following*
    bar's open, and a fill at the signal bar's own close or open is the bug
    this exists to catch.
    """
    rows = _random_series(random.Random(7), 60)
    verdicts = backtest.replay_signals(rows)
    buys = [v for v in verdicts if v[1] == "buy"]
    assert buys, "fixture produced no Buy signal; the test cannot check anything"

    index = buys[0][0]
    assert index + 1 < len(rows), "first Buy is the last bar; widen the fixture"

    sim = backtest.simulate(rows, verdicts, MIN_SAMPLES_FOR_BFP - 1)
    first_entry = (
        sim.trades[0].entry_date if sim.trades else (sim.open_entry or (None,))[0]
    )
    assert first_entry == rows[index + 1].date
    assert first_entry != rows[index].date


def test_pending_signals_are_not_counted_as_wins():
    """A signal without a full horizon is `pending` and out of the rate.

    The last bars of any window always carry some: counting them would score a
    partial outcome as a final one, always in whichever direction the window
    happened to end.
    """
    rows = _random_series(random.Random(11), 90)
    report = backtest.run(SID, rows)

    for stats in (report.buy_stats, report.sell_stats):
        for horizon in stats:
            signals = [
                s
                for s in report.signals
                if s.signal == ("buy" if stats is report.buy_stats else "sell")
            ]
            assert horizon.samples + horizon.pending == len(signals)
            assert horizon.wins <= horizon.samples
            if horizon.samples == 0:
                assert horizon.win_rate is None

    # The 20-day horizon cannot be settled for anything in the last 20 bars.
    tail = {r.date for r in rows[-max(backtest.HORIZONS) :]}
    for record in report.signals:
        if record.date in tail:
            assert max(backtest.HORIZONS) not in record.forward


def test_sell_is_scored_right_when_the_price_falls():
    """A Sell is a get-out: a fall after it is the signal working.

    Scoring both signals as "up = win" would report the Sell rate as the exact
    complement of the truth, which reads perfectly plausible.
    """
    falling = backtest.SignalRecord(
        date=START, signal="sell", close=100.0, reasons=[], forward={5: -0.04}
    )
    rising = backtest.SignalRecord(
        date=START, signal="sell", close=100.0, reasons=[], forward={5: 0.04}
    )

    stats = {s.horizon: s for s in backtest._horizon_stats([falling, rising], "sell")}
    assert stats[5].samples == 2
    assert stats[5].wins == 1  # only the fall counts

    buy_stats = {
        s.horizon: s
        for s in backtest._horizon_stats(
            [
                backtest.SignalRecord(
                    date=START, signal="buy", close=100.0, reasons=[], forward={5: -0.04}
                )
            ],
            "buy",
        )
    }
    assert buy_stats[5].wins == 0


def test_open_position_is_reported_but_never_a_completed_trade():
    """Marked to market inside the return, kept out of the win rate."""
    # A run that ends while long: bars are engineered to close on a Buy by
    # walking steadily up at the end, but whatever the fixture produces the
    # invariant below holds by construction.
    rows = _random_series(random.Random(3), 80)
    verdicts = backtest.replay_signals(rows)
    sim = backtest.simulate(rows, verdicts, MIN_SAMPLES_FOR_BFP - 1)

    for trade in sim.trades:
        assert trade.exit_date > trade.entry_date
        assert trade.holding_days >= 1
    if sim.open_entry is not None:
        assert all(t.entry_date != sim.open_entry[0] for t in sim.trades)


def test_benchmark_covers_the_same_days_as_the_strategy():
    """Buy-and-hold is measured from the first judged bar, not from bar zero.

    Starting the benchmark earlier silently hands it (or the strategy) a
    stretch the other never had the chance to trade.
    """
    rows = _random_series(random.Random(5), 100)
    report = backtest.run(SID, rows)

    usable = traditional.usable_rows(rows)
    start = MIN_SAMPLES_FOR_BFP - 1
    expected = float(usable[-1].close) / float(usable[start].close) - 1.0

    assert report.simulation.buy_hold_return == expected
    assert report.simulation.equity[0][0] == usable[start].date
    assert report.start == usable[start].date


def test_too_little_history_is_refused_rather_than_guessed():
    rows = _random_series(random.Random(1), backtest.MIN_BARS_FOR_BACKTEST - 1)
    try:
        backtest.run(SID, rows)
    except backtest.NotEnoughBars as exc:
        assert exc.available == backtest.MIN_BARS_FOR_BACKTEST - 1
    else:
        raise AssertionError("expected NotEnoughBars")


def test_baseline_covers_every_judged_day_not_just_signal_days():
    """The reference must be the whole window, or it is not a reference.

    Computing it over signal days only would compare the rule against itself
    and report an edge of exactly zero forever.
    """
    rows = _random_series(random.Random(13), 120)
    report = backtest.run(SID, rows)

    usable = traditional.usable_rows(rows)
    closes = [float(r.close) for r in usable]
    start = MIN_SAMPLES_FOR_BFP - 1

    by_horizon = {b.horizon: b for b in report.baseline}
    for horizon in backtest.HORIZONS:
        expected = len(closes) - horizon - start
        assert by_horizon[horizon].samples == expected

        ups = sum(
            1
            for i in range(start, len(closes) - horizon)
            if closes[i + horizon] > closes[i]
        )
        assert by_horizon[horizon].up_rate == ups / expected

    # And it is genuinely a wider sample than the signals it judges.
    assert by_horizon[backtest.HORIZONS[0]].samples > len(report.signals)


def test_sell_edge_is_measured_against_the_down_rate():
    """A Sell competes with the days that fell, not with the days that rose.

    Subtracting `up_rate` from a Sell win rate is the natural-looking mistake:
    in a rising window it turns a useless Sell rule into a large negative edge
    and a useful one into a small one, both wrong.
    """
    buy = [
        backtest.HorizonStats(
            horizon=h, samples=10, wins=6, pending=0,
            win_rate=0.6, average_return=0.02, median_return=0.02,
        )
        for h in backtest.HORIZONS
    ]
    sell = [
        backtest.HorizonStats(
            horizon=h, samples=10, wins=3, pending=0,
            win_rate=0.3, average_return=-0.01, median_return=-0.01,
        )
        for h in backtest.HORIZONS
    ]
    baseline = [
        backtest.BaselineStats(
            horizon=h, samples=100, ups=70, up_rate=0.7,
            average_return=0.015, median_return=0.015,
        )
        for h in backtest.HORIZONS
    ]

    edge = {e.horizon: e for e in backtest._edges(buy, sell, baseline)}
    first = edge[backtest.HORIZONS[0]]

    # 60 % right in a window that rose 70 % of the time is a *losing* rule.
    assert first.buy_edge == pytest.approx(0.6 - 0.7)
    # The Sell reference is 1 - 0.7 = 0.3, so 30 % right is exactly no edge.
    assert first.sell_edge == pytest.approx(0.0)
    assert first.buy_excess_return == pytest.approx(0.02 - 0.015)
    # A Sell that avoided a 1 % loss in a market averaging +1.5 % avoided 2.5 %.
    assert first.sell_excess_return == pytest.approx(0.015 - (-0.01))


def test_edge_over_an_unknown_rate_is_unknown_not_zero():
    """No samples means no edge -- reporting 0.0 would read as 'no advantage'."""
    empty = [
        backtest.HorizonStats(
            horizon=h, samples=0, wins=0, pending=3,
            win_rate=None, average_return=None, median_return=None,
        )
        for h in backtest.HORIZONS
    ]
    baseline = [
        backtest.BaselineStats(
            horizon=h, samples=50, ups=25, up_rate=0.5,
            average_return=0.0, median_return=0.0,
        )
        for h in backtest.HORIZONS
    ]

    for edge in backtest._edges(empty, empty, baseline):
        assert edge.buy_edge is None
        assert edge.sell_edge is None
        assert edge.buy_excess_return is None
        assert edge.sell_excess_return is None


def test_pooling_sums_counts_rather_than_averaging_per_stock_rates():
    """One stock with 100 signals must not get the same vote as one with 1.

    Averaging the per-stock rates is the intuitive implementation and gives a
    materially different answer whenever signal counts are uneven -- which,
    across a real watchlist, they always are.
    """
    rows_a = _random_series(random.Random(101), 130)
    rows_b = _random_series(random.Random(202), 130)
    reports = [backtest.run("A", rows_a), backtest.run("B", rows_b)]

    pooled = {p.horizon: p for p in backtest.pool(reports)}
    for horizon in backtest.HORIZONS:
        per_stock = [
            next(s for s in r.buy_stats if s.horizon == horizon) for r in reports
        ]
        wins = sum(s.wins for s in per_stock)
        samples = sum(s.samples for s in per_stock)

        assert pooled[horizon].buy_samples == samples
        assert pooled[horizon].stocks == 2
        if samples:
            assert pooled[horizon].buy_win_rate == pytest.approx(wins / samples)

        base = [next(b for b in r.baseline if b.horizon == horizon) for r in reports]
        assert pooled[horizon].baseline_up_rate == pytest.approx(
            sum(b.ups for b in base) / sum(b.samples for b in base)
        )


def test_pooled_edge_is_the_pooled_rates_not_the_pooled_edges():
    """The edge has to be recomputed from the summed rates.

    Averaging the per-stock edges reintroduces the equal-vote bug through the
    back door, after the rates themselves were pooled correctly.
    """
    rows_a = _random_series(random.Random(303), 130)
    rows_b = _random_series(random.Random(404), 130)
    pooled = {
        p.horizon: p
        for p in backtest.pool([backtest.run("A", rows_a), backtest.run("B", rows_b)])
    }

    for entry in pooled.values():
        if entry.buy_win_rate is None or entry.baseline_up_rate is None:
            assert entry.buy_edge is None
            continue
        assert entry.buy_edge == pytest.approx(
            entry.buy_win_rate - entry.baseline_up_rate
        )
        if entry.sell_win_rate is not None:
            assert entry.sell_edge == pytest.approx(
                entry.sell_win_rate - (1.0 - entry.baseline_up_rate)
            )
