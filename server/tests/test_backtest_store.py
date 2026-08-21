"""Pins what the cache layer adds on top of the engine.

`backtest.py` is replayed and pooled by its own tests; `backtest_store.py` only
turns a report into a row and back. What is worth pinning there is whatever a
wrong answer would still render as a tidy percentage:

  * **`computed_through` is the newest bar the replay saw.** It is the whole
    staleness rule -- a row is rebuilt when the stock has traded since, not on
    a timer. Stamp it with anything else (a clock, the window's end date) and
    the cache either never refreshes or refreshes constantly, and both look
    fine from the outside.
  * **An open position is not a trade and not a win.** It is inside
    `strategy_return`, marked to the last close. Counting it in `trade_wins`
    would inflate the win rate by exactly the trades that have not had to be
    closed at a loss yet.

  * **A row maps back to the response it came from.** Every field crosses a
    JSONB boundary as a plain dict, so a renamed payload key does not fail at
    the write -- it fails at the next read, for the rows written before it.

No database anywhere here: `_to_row` and `_to_response` are both pure, and the
write path (JSONB plus an ON CONFLICT upsert) is PostgreSQL-only, which is why
the round trip is pinned rather than the insert.
"""

from __future__ import annotations

import datetime

from app.models import BacktestResult
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


def test_a_stored_row_maps_back_to_the_response_it_was_built_from():
    """Round-trips `_to_row` through `_to_response` without a database.

    The write path needs PostgreSQL (JSONB, and an ON CONFLICT upsert), so this
    is the only place the mapping itself can be pinned. It is worth pinning:
    every field crosses a JSONB boundary as a plain dict, so a renamed payload
    key does not fail at the write, it fails at the next read -- and only for
    the stocks whose rows were written before the rename.
    """
    report = _report(trades=[_trade(True), _trade(False)], open_entry=None)
    row_values = backtest_store._to_row(report)

    # A detached ORM instance is enough: `_to_response` only reads attributes.
    row = BacktestResult(**row_values)
    out = backtest_store._to_response(row, name="台積電", cached=True)

    assert out.sid == "2330"
    assert out.name == "台積電"
    assert out.cached is True
    assert out.window_months == row_values["window_months"]
    assert out.computed_through == report.end

    sim = out.simulation
    assert sim.trade_count == 2
    assert sim.winning_trades == 1
    assert sim.trade_win_rate == 0.5
    assert sim.strategy_return == report.simulation.strategy_return
    assert sim.buy_hold_return == report.simulation.buy_hold_return
    assert sim.open_entry_date is None
    # The curve survives the JSONB round trip with both series intact.
    assert [(p.strategy, p.buy_hold) for p in sim.equity] == [(1.0, 1.0), (1.1, 1.2)]


def test_no_completed_trades_reports_a_null_win_rate_not_zero():
    row = BacktestResult(**backtest_store._to_row(_report(trades=[])))
    out = backtest_store._to_response(row, name="台積電", cached=False)

    assert out.simulation.trade_count == 0
    assert out.simulation.trade_win_rate is None
    assert out.cached is False


def test_window_start_is_month_aligned():
    """Matches how `/history` buckets months, so both read the same range."""
    start = backtest_store.window_start(datetime.date(2026, 8, 21))
    assert start.day == 1
    assert start == datetime.date(2025, 9, 1)  # 12 buckets back, inclusive
