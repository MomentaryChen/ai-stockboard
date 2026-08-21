"""Pins the arithmetic the 存股 checklist is built on.

Every number here is one a rule downstream compares against a threshold, so a
quiet change in how it is computed moves verdicts without moving any rule. The
cases worth pinning are the ones where the obvious implementation is wrong:

  * the current calendar year is excluded from every streak, or a company that
    pays in August looks lapsed for the first eight months of every year;
  * `unknown` and zero stay distinct all the way through, because the whole
    coverage story downstream depends on the difference;
  * a session that traded nothing is counted, not skipped, since that is
    exactly what the liquidity dimension is looking for.
"""

from __future__ import annotations

import datetime

from app.models import DailyPrice, DividendEvent, FundamentalsAnnual
from app.services.analysis import hold_features

SID = "2880"
AS_OF = datetime.date(2026, 3, 20)


def _prices(
    n: int = 300,
    *,
    close: float = 20.0,
    capacity: int = 2_000_000,
    end: datetime.date = AS_OF,
) -> list[DailyPrice]:
    """`n` consecutive sessions ending on `end`, all identical unless overridden."""
    return [
        DailyPrice(
            sid=SID,
            date=end - datetime.timedelta(days=n - 1 - i),
            open=close,
            high=close,
            low=close,
            close=close,
            capacity=capacity,
            turnover=int(capacity * close),
        )
        for i in range(n)
    ]


def _cash(year: int, amount: float, month: int = 8) -> DividendEvent:
    return DividendEvent(
        sid=SID,
        ex_date=datetime.date(year, month, 15),
        name="test",
        kind="息",
        cash_dividend=amount,
    )


def _extract(**overrides):
    kwargs = dict(
        sid=SID,
        name="test",
        industry="金融保險業",
        prices=_prices(),
        dividends=[],
        coverage="history",
        observed_dividend_years=set(range(2010, 2027)),
        fundamentals=[],
    )
    kwargs.update(overrides)
    return hold_features.extract(**kwargs)


# --- dividends ----------------------------------------------------------------


def test_the_current_year_is_never_counted_against_a_payout_streak():
    """Asked in March, a company that pays every August has not lapsed.

    The single most damaging off-by-one available here: counting the current
    year would break every streak on the board from January to the ex-date.
    """
    events = [_cash(year, 1.0) for year in range(2017, 2026)]  # through 2025
    features = _extract(dividends=events)

    # 2017..2025 is nine complete years, all paying, and 2026 is not judged.
    assert features.dividend.consecutive_years_with_cash == 9
    assert features.dividend.years_with_cash == 9


def test_the_current_year_does_count_once_it_has_actually_paid():
    """The other half of the same rule: a streak must not lag its own evidence.

    Excluded while the payment is still pending, included the moment it goes
    ex -- at which point there is nothing left to wait for.
    """
    events = [_cash(y, 1.0) for y in range(2020, 2026)] + [_cash(2026, 1.0, month=2)]
    features = _extract(dividends=events)

    assert features.dividend.consecutive_years_with_cash == 7
    assert features.dividend.latest_ex_date == datetime.date(2026, 2, 15)


def test_a_missed_year_ends_the_streak_but_not_the_count():
    events = [_cash(y, 1.0) for y in (2019, 2020, 2022, 2023, 2024, 2025)]
    features = _extract(dividends=events)

    assert features.dividend.years_with_cash == 6
    # 2021 paid nothing, so only 2022-2025 is unbroken back from 2025.
    assert features.dividend.consecutive_years_with_cash == 4


def test_ttm_cash_is_the_trailing_twelve_months_not_the_calendar_year():
    """The yield on the card has to mean the same thing in January as in December."""
    features = _extract(
        dividends=[
            _cash(2025, 1.5, month=8),  # inside the window
            _cash(2024, 9.9, month=8),  # outside it
        ]
    )
    assert features.dividend.ttm_cash == 1.5
    assert features.dividend.cash_yield_pct == 7.5  # 1.5 / 20.0


def test_an_announced_but_not_yet_traded_ex_date_is_not_cash_received():
    upcoming = _cash(2026, 2.0, month=8)  # after AS_OF
    features = _extract(dividends=[upcoming])

    assert features.dividend.ttm_cash is None
    assert features.dividend.latest_ex_date is None


def test_two_payments_in_one_year_sum_rather_than_counting_twice():
    features = _extract(
        dividends=[_cash(2025, 0.6, month=3), _cash(2025, 0.9, month=9)]
    )
    assert features.dividend.years_with_cash == 1
    assert features.dividend.avg_cash_per_year == 1.5


def test_a_company_that_never_paid_reports_zero_rather_than_null():
    """Zero paying years is a finding; null would read as 'not looked at'."""
    features = _extract(dividends=[])
    assert features.dividend.years_with_cash == 0
    assert features.dividend.ttm_cash is None
    assert features.dividend.cash_yield_pct is None


# --- liquidity ----------------------------------------------------------------


def test_sessions_with_no_trade_are_counted_not_skipped():
    """A name that trades one day in three must not report the average of those."""
    rows = _prices(60)
    for row in rows[:20]:
        row.capacity = 0
    features = _extract(prices=rows)

    assert features.liquidity.trading_days == 60
    assert features.liquidity.no_trade_days == 20
    # 40 sessions at 2m shares, 20 at nothing.
    assert features.liquidity.avg_daily_shares == int(40 * 2_000_000 / 60)


def test_liquidity_reads_only_the_recent_window():
    rows = _prices(400, capacity=1_000)
    for row in rows[-hold_features.LIQUIDITY_WINDOW:]:
        row.capacity = 3_000_000
    features = _extract(prices=rows)

    assert features.liquidity.trading_days == hold_features.LIQUIDITY_WINDOW
    assert features.liquidity.avg_daily_shares == 3_000_000


# --- fundamentals -------------------------------------------------------------


def _annual(year: int, eps: float | None, roe: float | None = None):
    return FundamentalsAnnual(sid=SID, year=year, eps=eps, roe=roe)


def test_an_empty_fundamentals_table_produces_nulls_not_zeroes():
    """The state everything ships in, and the one the checklist must not score."""
    f = _extract().fundamentals
    assert f.years_available == 0
    assert f.eps_years_checked == 0
    assert f.eps_positive_years is None
    assert f.avg_roe_pct is None
    assert f.trailing_pe is None


def test_loss_years_are_counted_and_do_not_break_the_average():
    rows = [_annual(y, 2.0) for y in range(2019, 2025)] + [_annual(2025, -1.0)]
    f = _extract(fundamentals=rows).fundamentals

    assert f.eps_years_checked == 7
    assert f.eps_positive_years == 6
    assert f.latest_eps == -1.0
    assert f.latest_eps_year == 2025


def test_trailing_pe_is_undefined_rather_than_negative_on_a_loss_year():
    """A negative PE is not a cheap stock, and rendering one would say it was."""
    f = _extract(fundamentals=[_annual(2025, -1.0)]).fundamentals
    assert f.latest_eps == -1.0
    assert f.trailing_pe is None

    f = _extract(fundamentals=[_annual(2025, 2.0)]).fundamentals
    assert f.trailing_pe == 10.0  # close 20.0 / EPS 2.0


def test_roe_spread_needs_two_years_and_is_null_rather_than_zero_on_one():
    """One year has no spread; reporting 0 would read as perfectly stable."""
    one = _extract(fundamentals=[_annual(2025, 2.0, roe=12.0)]).fundamentals
    assert one.avg_roe_pct == 12.0
    assert one.roe_stdev_pct is None

    two = _extract(
        fundamentals=[_annual(2024, 2.0, roe=8.0), _annual(2025, 2.0, roe=12.0)]
    ).fundamentals
    assert two.avg_roe_pct == 10.0
    assert two.roe_stdev_pct is not None


def test_the_current_year_is_excluded_from_fundamentals_too():
    """A part-year EPS filed early must not be read as the year's result."""
    f = _extract(fundamentals=[_annual(2026, 0.2), _annual(2025, 3.0)]).fundamentals
    assert f.latest_eps_year == 2025
    assert f.eps_years_checked == 1
    # ...but the row is still stored, and the count says so.
    assert f.years_available == 2


# --- the snapshot as a whole --------------------------------------------------


def test_no_bars_leaves_as_of_null_and_still_reads_the_dividend_record():
    """No price history is not a reason to refuse the half we do have."""
    features = _extract(prices=[], dividends=[_cash(2025, 1.0)])

    assert features.as_of is None
    assert features.price.latest_close is None
    # Without a close there is no yield, but the payout record still counts.
    assert features.dividend.cash_yield_pct is None
    assert features.dividend.years_with_cash == 1


def test_position_in_the_long_range_is_read_over_years_not_a_quarter():
    rows = _prices(400, close=10.0)
    for row in rows[-30:]:
        row.close = row.open = row.high = row.low = 20.0
    features = _extract(prices=rows)

    assert features.price.window_low == 10.0
    assert features.price.window_high == 20.0
    assert features.price.position_pct == 100.0
    assert features.price.drawdown_from_high_pct == 0.0
