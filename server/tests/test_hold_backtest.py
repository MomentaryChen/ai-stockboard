"""Pins the arithmetic of the long gradesheet.

Every number this engine produces is a return someone might act on, and all of
them are the kind that look plausible when wrong. The cases below are the ones
where the obvious implementation gives a confident wrong answer:

  * a 股票股利 raises the share count *and* drops the unadjusted price. Handle
    only one side and the same event reads as a 9% loss or a 10x gain;
  * reinvestment compounds, so the dividend contribution is not the sum of the
    dividends;
  * an ex-dividend gap that has not had time to fill is not an ex-dividend gap
    that failed to fill;
  * two years is the floor, because an annualised rate from six months turns
    one good quarter into a decade-long expectation.
"""

from __future__ import annotations

import datetime

import pytest

from app.models import DailyPrice, DividendEvent
from app.services.analysis import hold_backtest

SID = "2880"
START = datetime.date(2020, 1, 1)


def _bars(n: int, price=None, start: datetime.date = START) -> list[DailyPrice]:
    """`n` consecutive sessions. `price` may be a constant or a callable."""
    def at(i: int) -> float:
        if price is None:
            return 100.0
        return float(price(i)) if callable(price) else float(price)

    return [
        DailyPrice(
            sid=SID,
            date=start + datetime.timedelta(days=i),
            open=at(i), high=at(i), low=at(i), close=at(i),
            capacity=1_000_000, turnover=100_000_000,
        )
        for i in range(n)
    ]


def _event(offset: int, *, cash: float = 0.0, stock: float = 0.0) -> DividendEvent:
    return DividendEvent(
        sid=SID,
        ex_date=START + datetime.timedelta(days=offset),
        name="test",
        kind="息",
        cash_dividend=cash or None,
        stock_dividend=stock or None,
    )


#: `_bars` emits one bar per *calendar* day, and the engine measures elapsed
#: time from the calendar rather than from the bar count -- so this is a span
#: in days, comfortably over the two-year floor, not a session count.
SPAN = 800


# --- the floor ----------------------------------------------------------------


def test_a_short_history_is_refused_rather_than_annualised():
    """Six months of bars cannot produce a ten-year expectation."""
    with pytest.raises(hold_backtest.NotEnoughBars):
        hold_backtest.run(SID, "test", _bars(120), [])


def test_the_window_reported_is_the_window_replayed():
    """It is bounded by stored bars, so it has to say what it actually used."""
    rows = _bars(SPAN + 60)
    result = hold_backtest.run(SID, "test", rows, [])

    assert result.bars == len(rows)
    assert result.start == rows[0].date
    assert result.end == rows[-1].date
    assert result.years == round(
        (rows[-1].date - rows[0].date).days / hold_backtest.DAYS_PER_YEAR, 2
    )


def test_a_long_span_with_almost_no_bars_is_refused_too():
    """Thirty sessions across three years is a three-year span and nothing to
    annualise. The floor is density as well as length."""
    sparse = [
        DailyPrice(
            sid=SID, date=START + datetime.timedelta(days=i * 40),
            open=100, high=100, low=100, close=100,
            capacity=1000, turnover=100000,
        )
        for i in range(30)
    ]
    assert (sparse[-1].date - sparse[0].date).days > 2 * 365
    with pytest.raises(hold_backtest.NotEnoughBars):
        hold_backtest.run(SID, "test", sparse, [])


# --- returns ------------------------------------------------------------------


def test_a_flat_price_with_no_payout_returns_nothing_either_way():
    result = hold_backtest.run(SID, "test", _bars(SPAN), [])

    assert result.price_return_pct == 0.0
    assert result.total_return_pct == 0.0
    assert result.dividend_return_pct == 0.0
    assert result.cash_collected == 0.0
    assert result.max_drawdown_pct == 0.0


def test_cash_dividends_buy_more_shares_and_show_up_as_the_difference():
    """Flat price, so every point of total return came from the payouts."""
    rows = _bars(SPAN)
    # Two 5-NTD payouts on a 100 price: 5% each, reinvested.
    events = [_event(100, cash=5.0), _event(400, cash=5.0)]
    result = hold_backtest.run(SID, "test", rows, events)

    assert result.price_return_pct == 0.0
    # 1.05 * 1.05 - 1 = 10.25%, not the 10% a naive sum would report.
    assert result.total_return_pct == pytest.approx(10.25, abs=0.01)
    assert result.dividend_return_pct == pytest.approx(10.25, abs=0.01)
    # Cash *received* compounds too: the second payout is paid on more shares.
    assert result.cash_collected == pytest.approx(5.0 + 5.25, abs=0.01)


def test_reinvestment_compounds_rather_than_adding_up():
    """The distinction the `dividend_return_pct` docstring is about."""
    rows = _bars(SPAN)
    events = [_event(60 * i, cash=5.0) for i in range(1, 6)]
    result = hold_backtest.run(SID, "test", rows, events)

    naive = 5 * 5.0 / 100 * 100  # 25% if the payouts merely added up
    assert result.total_return_pct > naive


def test_a_stock_dividend_is_shares_per_ten_held_not_per_share():
    """The convention that inflates a return tenfold when read wrongly.

    股票股利 of 1.0 means one new share per ten held -- a 10% increase, not a
    100% one.
    """
    rows = _bars(SPAN)
    result = hold_backtest.run(SID, "test", rows, [_event(100, stock=1.0)])

    assert result.total_return_pct == pytest.approx(10.0, abs=0.01)


def test_a_stock_dividend_offsets_the_price_drop_it_causes():
    """`daily_price` is unadjusted, so this is not optional book-keeping.

    A 1.0 股票股利 drops the quoted price by about 9.09% (100 -> 100/1.1).
    Counting the price drop without the extra shares reports a loss the holder
    never took.
    """
    split_at = 100
    rows = _bars(SPAN, price=lambda i: 100.0 if i < split_at else 100.0 / 1.1)
    result = hold_backtest.run(SID, "test", rows, [_event(split_at, stock=1.0)])

    # The price series alone looks like a 9% fall...
    assert result.price_return_pct == pytest.approx(-9.09, abs=0.05)
    # ...and the holder is exactly square.
    assert result.total_return_pct == pytest.approx(0.0, abs=0.05)


def test_annualised_return_compounds_rather_than_dividing():
    """A 21% gain over two years is ~10%/yr, not 10.5%.

    Spanned exactly two calendar years, because that is now what the engine
    divides by -- see `_span_years`.
    """
    two_years = int(2 * hold_backtest.DAYS_PER_YEAR) + 2
    rows = _bars(two_years, price=lambda i: 100.0 if i < two_years - 1 else 121.0)
    result = hold_backtest.run(SID, "test", rows, [])

    assert result.years == pytest.approx(2.0, abs=0.01)
    assert result.total_return_pct == pytest.approx(21.0, abs=0.01)
    assert result.annualised_return_pct == pytest.approx(10.0, abs=0.1)


def test_yield_on_cost_is_measured_against_the_original_price():
    """The number that keeps rising because the cost never moves."""
    rows = _bars(SPAN, price=lambda i: 100.0 + i * 0.1)
    # Only the final payout is inside the trailing year.
    events = [_event(30, cash=4.0), _event(SPAN - 30, cash=6.0)]
    result = hold_backtest.run(SID, "test", rows, events)

    # 6 against the *start* price of 100, not against the much higher end price.
    assert result.yield_on_cost_pct == pytest.approx(6.0, abs=0.2)


# --- drawdown -----------------------------------------------------------------


def test_max_drawdown_is_the_worst_fall_from_a_peak_not_from_the_start():
    rows = _bars(
        SPAN,
        price=lambda i: 100.0 if i < 100 else (150.0 if i < 200 else 120.0),
    )
    result = hold_backtest.run(SID, "test", rows, [])

    # Peak 150 -> 120 is -20%, worse than start 100 -> 120 being positive.
    assert result.max_drawdown_pct == pytest.approx(-20.0, abs=0.1)


# --- 填息 ---------------------------------------------------------------------


def test_a_gap_that_closes_counts_as_filled_with_the_days_it_took():
    """The reference is the close before the ex-date -- what the holder watched
    the price fall away from."""
    ex = 100
    def price(i):
        if i < ex:
            return 100.0
        if i < ex + 10:
            return 95.0   # gapped down and drifting
        return 101.0      # recovered
    result = hold_backtest.run(SID, "test", _bars(SPAN, price=price),
                               [_event(ex, cash=5.0)])

    assert result.fill.events == 1
    assert result.fill.filled == 1
    assert result.fill.fill_rate_pct == 100.0
    assert result.fill.median_days_to_fill == 10


def test_a_gap_that_never_closes_counts_against_the_rate():
    """A dividend whose gap never fills is the holder's own capital returned."""
    ex = 100
    result = hold_backtest.run(
        SID, "test",
        _bars(SPAN, price=lambda i: 100.0 if i < ex else 90.0),
        [_event(ex, cash=5.0)],
    )
    assert result.fill.events == 1
    assert result.fill.filled == 0
    assert result.fill.fill_rate_pct == 0.0


def test_a_gap_still_inside_its_window_is_pending_not_failed():
    """Same rule the short backtest applies to a signal whose horizon has not
    elapsed: counting it as a miss would punish every recent payout."""
    rows = _bars(SPAN)
    # Ex-date a fortnight before the series ends, well inside the fill window.
    ex_offset = SPAN - 14
    result = hold_backtest.run(
        SID, "test",
        _bars(SPAN, price=lambda i: 100.0 if i < ex_offset else 90.0),
        [_event(ex_offset, cash=5.0)],
    )
    assert result.fill.pending == 1
    assert result.fill.filled == 0
    # Nothing has been judged, so there is no rate to report.
    assert result.fill.fill_rate_pct is None
    assert len(rows) == SPAN


def test_a_stock_only_payout_is_not_counted_as_an_ex_dividend_gap():
    """填息 is about cash. A share split has no income to fill."""
    result = hold_backtest.run(
        SID, "test", _bars(SPAN), [_event(100, stock=1.0)]
    )
    assert result.fill.events == 0
    assert result.stock_dividend_years == 1


# --- payload ------------------------------------------------------------------


def test_the_curve_is_thinned_but_still_ends_where_the_totals_say():
    """A decade of daily bars is 2,500 points for a 300px sparkline."""
    rows = _bars(SPAN * 2, price=lambda i: 100.0 + i * 0.05)
    result = hold_backtest.run(SID, "test", rows, [])

    assert len(result.curve) <= hold_backtest.MAX_CURVE_POINTS
    assert result.curve[-1].date == result.end
    # The last point of the curve must agree with the headline return.
    assert result.curve[-1].total == pytest.approx(
        100 + result.total_return_pct, abs=0.05
    )


def test_payouts_outside_the_window_are_ignored():
    rows = _bars(SPAN)
    outside = _event(-30, cash=99.0)
    result = hold_backtest.run(SID, "test", rows, [outside])

    assert result.cash_collected == 0.0
    assert result.total_return_pct == 0.0


# --- benchmark ----------------------------------------------------------------


def test_the_benchmark_is_compared_against_price_not_total_return():
    """加權指數 is a price index. Measuring a dividend-reinvested return
    against it would flatter every stock on the board by roughly its own
    yield, every year -- so the excess is taken against the price leg."""
    rows = _bars(SPAN, price=lambda i: 100.0 + i * 0.05)      # +40% over the span
    index = _bars(SPAN, price=lambda i: 1000.0 + i * 0.25)    # +20% over the same
    result = hold_backtest.run(
        SID, "test", rows, [_event(100, cash=5.0)],
        index_rows=index, index_sid="t00",
    )

    assert result.index_sid == "t00"
    assert result.index_return_pct == pytest.approx(20.0, abs=0.1)
    # Excess is price - index, and deliberately not total - index.
    assert result.excess_price_return_pp == pytest.approx(
        result.price_return_pct - result.index_return_pct, abs=0.01
    )
    assert result.total_return_pct > result.price_return_pct


def test_the_benchmark_is_bounded_to_the_stock_s_own_window():
    """A benchmark measured over a different period is not a benchmark."""
    rows = _bars(SPAN, price=lambda i: 100.0 + i * 0.05, start=START)
    # Index history starts a year earlier and runs a year longer.
    longer = _bars(SPAN + 730, price=lambda i: 1000.0 + i * 0.25,
                   start=START - datetime.timedelta(days=365))
    result = hold_backtest.run(SID, "test", rows, [], index_rows=longer,
                               index_sid="t00")

    # The index rose steadily throughout, so its whole-series return is far
    # larger than its return over the stock's window. Only the slice counts.
    whole_series = (longer[-1].close / longer[0].close - 1) * 100
    slice_only = (
        [r for r in longer if r.date == rows[-1].date][0].close
        / [r for r in longer if r.date == rows[0].date][0].close - 1
    ) * 100

    assert whole_series > slice_only + 15  # they are nowhere near each other
    assert result.index_return_pct == pytest.approx(slice_only, abs=0.05)


def test_no_stored_index_bars_means_no_benchmark_rather_than_a_zero():
    """Zero would read as "the market went nowhere", which is a claim."""
    result = hold_backtest.run(SID, "test", _bars(SPAN), [], index_rows=[])
    assert result.index_return_pct is None
    assert result.index_sid is None
    assert result.excess_price_return_pp is None


def test_an_index_that_barely_overlaps_reports_nothing():
    rows = _bars(SPAN)
    stray = _bars(3, price=1000.0, start=START - datetime.timedelta(days=900))
    result = hold_backtest.run(SID, "test", rows, [], index_rows=stray,
                               index_sid="t00")
    assert result.index_return_pct is None
