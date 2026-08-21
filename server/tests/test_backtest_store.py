"""Pins what the cache layer adds on top of the engine.

`backtest.py` is replayed and pooled by its own tests; `backtest_store.py` only
turns a report into a row and back. Two things there are worth pinning because
a wrong answer still looks like a tidy percentage:

  * **`computed_through` is the newest bar the replay saw.** It is the whole
    staleness rule -- a row is rebuilt when the stock has traded since, not on
    a timer. Stamp it with anything else (a clock, the window's end date) and
    the cache either never refreshes or refreshes constantly, and both look
    fine from the outside.
  * **An open position is not a trade and not a win.** It is inside
    `strategy_return`, marked to the last close. Counting it in `trade_wins`
    would inflate the win rate by exactly the trades that have not had to be
    closed at a loss yet.

No database: both go through `_to_row`, which is pure.
"""

from __future__ import annotations

import datetime

from app.services import backtest_store
from app.services.analysis import backtest

START = datetime.date(2025, 1, 6)


def _report(
    *,
    trades: list[backtest.Trade],
    open_entry: tuple[datetime.date, float] | None = None,
    end: datetime.date = datetime.date(2026, 8, 20),
) -> backtest.BacktestReport:
    """A report with only the fields `_to_row` reads. Engine untouched."""
    simulation = backtest.Simulation(
        trades=trades,
        equity=[(START, 1.0, 1.0), (end, 1.1, 1.2)],
        strategy_return=0.1,
        buy_hold_return=0.2,
        max_drawdown=-0.05,
        buy_hold_max_drawdown=-0.08,
        open_entry=open_entry,
        exposure=0.3,
    )
    return backtest.BacktestReport(
        sid="2330",
        rule_set="grs",
        start=START,
        end=end,
        bars=240,
        judged_days=229,
        signals=[],
        buy_stats=[],
        sell_stats=[],
        baseline=[],
        edges=[],
        simulation=simulation,
    )


def _trade(profit_up: bool, holding_days: int = 5) -> backtest.Trade:
    return backtest.Trade(
        entry_date=START,
        entry_price=100.0,
        exit_date=START + datetime.timedelta(days=holding_days),
        exit_price=110.0 if profit_up else 90.0,
        holding_days=holding_days,
    )


def test_computed_through_is_the_last_bar_replayed():
    """The staleness key. Anything else breaks the refresh rule silently."""
    end = datetime.date(2026, 8, 20)
    row = backtest_store._to_row(_report(trades=[], end=end))

    assert row["computed_through"] == end
    assert row["end_date"] == end


def test_only_closed_profitable_trades_count_as_wins():
    row = backtest_store._to_row(
        _report(trades=[_trade(True), _trade(False), _trade(True)])
    )

    assert row["trades"] == 3
    assert row["trade_wins"] == 2


def test_an_open_position_is_neither_a_trade_nor_a_win():
    """It is inside `strategy_return`; it must not reach the trade stats."""
    open_at = (datetime.date(2026, 8, 11), 15.03)
    row = backtest_store._to_row(_report(trades=[_trade(True)], open_entry=open_at))

    assert row["trades"] == 1
    assert row["trade_wins"] == 1
    assert row["payload"]["open_position"] == {
        "entry_date": "2026-08-11",
        "entry_price": 15.03,
    }


def test_average_holding_days_is_null_when_nothing_closed():
    """Null, not 0 -- "held nothing" and "held for no time" are different."""
    assert backtest_store._to_row(_report(trades=[]))["payload"][
        "average_holding_days"
    ] is None

    row = backtest_store._to_row(
        _report(trades=[_trade(True, holding_days=4), _trade(False, holding_days=6)])
    )
    assert row["payload"]["average_holding_days"] == 5.0


def test_signals_are_stored_newest_first():
    """The response caps the list, so the cap has to keep the recent ones."""
    report = _report(trades=[])
    signals = [
        backtest.SignalRecord(
            date=START + datetime.timedelta(days=i),
            signal="buy",
            close=100.0,
            reasons=[],
            forward={5: 0.01},
        )
        for i in range(3)
    ]
    stored = backtest_store._to_row(
        backtest.BacktestReport(**{**vars(report), "signals": signals})
    )["payload"]["signals"]

    assert [s["date"] for s in stored] == [
        (START + datetime.timedelta(days=2)).isoformat(),
        (START + datetime.timedelta(days=1)).isoformat(),
        START.isoformat(),
    ]
    # JSONB keys are strings; the response model coerces them back to int.
    assert stored[0]["forward"] == {"5": 0.01}


def test_window_start_is_month_aligned():
    """Matches how `/history` buckets months, so both read the same range."""
    start = backtest_store.window_start(datetime.date(2026, 8, 21))
    assert start.day == 1
    assert start == datetime.date(2025, 9, 1)  # 12 buckets back, inclusive
