"""What accumulating and holding one stock actually returned.

The long gradesheet, and the reason the 存股 lane was built as a separate lane
at all. `backtest.py` next door replays a signal and reports how often it was
right over 5, 10 and 20 days. Ask that of a method whose answer is "buy it and
do nothing for a decade" and there is nothing to count: no trades, no hits, no
base rate to beat. The two engines need different scorecards or the comparison
between them is rigged in favour of whichever one trades.

So this measures what a holder experiences instead: total return with payouts
reinvested, how much of that came from the payouts rather than the price, the
worst fall along the way, and whether the dividends were real income or the
holder's own capital handed back.

Pure function of stored rows -- no session, no network, no clock -- like every
other engine here, so the numbers on the card can be reproduced from the same
bars and the same dividend rows.

## The simulation

One share bought at the first close, held to the last.

* **Cash dividends** buy more shares at the ex-date's close. Reinvestment is
  the method's own instruction, and it is also what makes the result a
  time-weighted return that can be compared between stocks.
* **Stock dividends** raise the share count directly. This is not optional
  book-keeping: `daily_price` is unadjusted, so a name that pays 股票股利 shows
  a price drop on the ex-date that never happened to its holder. Ignoring the
  share count would report that drop as a loss.

  The convention is Taiwanese and worth stating: 股票股利 is quoted in NTD per
  share against a 10 NTD face value, so `1.0` means one new share per ten
  held. Getting this backwards inflates a return by an order of magnitude,
  which is why it is asserted in the tests.

## What it does not model

Tax, 二代健保, brokerage fees and the odd-lot reality of reinvesting a few
hundred dollars of dividend. Every one of those makes the real result slightly
worse, so the figure here is an optimistic bound -- stated on the card rather
than buried, because a backtest that quietly flatters is worse than none.

It also does not simulate 定期定額. Buying monthly is a money-weighted
question with a different answer, and mixing the two would produce a number
that is neither.
"""

from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass

from app.models import DailyPrice, DividendEvent
from app.schemas import HoldBacktestResponse, HoldEquityPoint, HoldFillStats
from app.services.analysis import traditional

#: Shortest replay worth reporting. Under two years a "hold" result is mostly
#: the last market cycle, and quoting an annualised rate from it would turn
#: one good quarter into a decade-long expectation.
MIN_YEARS = 2.0

#: Sessions a year, on the exchange's calendar. Used only as a density floor,
#: never to measure elapsed time -- see `_span_years`.
TRADING_DAYS_PER_YEAR = 240

#: A window has to be *populated* as well as long. Half the usual density is
#: the floor: a name that traded thirty times in three years has a three-year
#: span and nothing worth annualising inside it.
MIN_BARS_PER_YEAR = TRADING_DAYS_PER_YEAR // 2

DAYS_PER_YEAR = 365.25

#: How long an ex-dividend gap gets to close before it counts as unfilled.
#: Six months: 填息 is conventionally discussed over "before the next payout",
#: and most Taiwanese payers are annual.
FILL_WINDOW_DAYS = 180

#: 股票股利 is quoted per share against a 10 NTD face value.
FACE_VALUE = 10.0

#: Points on the curve handed to the browser. The card draws a sparkline, not
#: a chart anyone reads a value off, so a decade of daily bars is ~2,500
#: points of payload for maybe 300 pixels.
MAX_CURVE_POINTS = 300


class NotEnoughBars(RuntimeError):
    """Fewer stored sessions than a multi-year statement needs."""


@dataclass(frozen=True, slots=True)
class _Payout:
    date: datetime.date
    cash: float
    stock: float


def _span_years(start: datetime.date, end: datetime.date) -> float:
    """Elapsed time, from the calendar rather than from the bar count.

    Dividing sessions by 240 is the tempting shortcut and it is wrong in the
    direction that matters. A thinly traded name -- exactly the kind the
    Liquid dimension fails and someone looks at anyway -- has gaps, so its bar
    count understates how long the money was actually tied up, and every
    annualised figure derived from it comes out too high. The calendar does
    not have that problem.
    """
    return (end - start).days / DAYS_PER_YEAR

def _payouts(events: list[DividendEvent], start: datetime.date, end: datetime.date):
    """Ex-dividend events inside the window, oldest first.

    Keyed on the ex-date because that is the day the holder's position changes
    -- the announcement is not money and the payment date is not when the
    price moves.
    """
    rows = []
    for event in events:
        if not (start <= event.ex_date <= end):
            continue
        cash = float(event.cash_dividend) if event.cash_dividend else 0.0
        stock = float(event.stock_dividend) if event.stock_dividend else 0.0
        if cash <= 0 and stock <= 0:
            continue
        rows.append(_Payout(date=event.ex_date, cash=cash, stock=stock))
    return sorted(rows, key=lambda p: p.date)


def _max_drawdown(values: list[float]) -> float:
    """Worst peak-to-trough fall, as a negative percentage."""
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return round(worst * 100, 2)


def _fill_stats(
    closes: dict[datetime.date, float],
    dates: list[datetime.date],
    payouts: list[_Payout],
) -> HoldFillStats:
    """How often, and how fast, the price closed the ex-dividend gap.

    The reference is the close on the last session *before* the ex-date --
    what a holder watched the price fall from. An event is filled the first
    session that closes at or above it.

    Events whose window has not fully elapsed are `pending` and excluded from
    the rate, the same way the short backtest excludes a signal whose horizon
    has not run. Counting them as failures would drag the rate down every time
    a company pays close to the end of the stored history.
    """
    if not dates:
        return HoldFillStats(
            events=0, filled=0, fill_rate_pct=None, median_days_to_fill=None,
            pending=0, window_days=FILL_WINDOW_DAYS,
        )

    last = dates[-1]
    filled_days: list[int] = []
    judged = 0
    pending = 0

    for payout in payouts:
        if payout.cash <= 0:
            continue
        before = [d for d in dates if d < payout.date]
        if not before:
            continue
        reference = closes[before[-1]]

        deadline = payout.date + datetime.timedelta(days=FILL_WINDOW_DAYS)
        hit: int | None = None
        for day in dates:
            if day < payout.date or day > deadline:
                continue
            if closes[day] >= reference:
                hit = (day - payout.date).days
                break

        if hit is not None:
            judged += 1
            filled_days.append(hit)
        elif last >= deadline:
            judged += 1
        else:
            # Still inside its window at the end of the series.
            pending += 1

    return HoldFillStats(
        events=judged + pending,
        filled=len(filled_days),
        fill_rate_pct=round(len(filled_days) / judged * 100, 1) if judged else None,
        median_days_to_fill=(
            int(statistics.median(filled_days)) if filled_days else None
        ),
        pending=pending,
        window_days=FILL_WINDOW_DAYS,
    )


def _thin(points: list[HoldEquityPoint], cap: int) -> list[HoldEquityPoint]:
    """Even sample, always keeping the last point so the end matches the totals."""
    if len(points) <= cap:
        return points
    step = len(points) / cap
    sampled = [points[int(i * step)] for i in range(cap)]
    if sampled[-1].date != points[-1].date:
        sampled[-1] = points[-1]
    return sampled


def _index_return(
    index_rows: list[DailyPrice], start: datetime.date, end: datetime.date
) -> float | None:
    """The market's price return over the same sessions, or None.

    Bounded to the stock's own window rather than the index's: a benchmark
    measured over a different period is not a benchmark. Both ends have to
    fall inside what the index has stored, or there is nothing honest to
    report and it stays null.
    """
    usable = [r for r in traditional.usable_rows(index_rows) if start <= r.date <= end]
    if len(usable) < 2:
        return None
    first, last = float(usable[0].close), float(usable[-1].close)
    if not first:
        return None
    return round((last / first - 1) * 100, 2)


def run(
    sid: str,
    name: str,
    rows: list[DailyPrice],
    events: list[DividendEvent],
    coverage: str = "history",
    index_rows: list[DailyPrice] | None = None,
    index_sid: str | None = None,
) -> HoldBacktestResponse:
    """Replay a buy-and-hold over whatever bars are stored.

    The window is not configurable and not fixed either -- it is however much
    history `daily_price` happens to hold for this stock, reported back as
    `start`, `end` and `years`. That is deliberate: this route is cache-only
    like the rest of the hold lane, and pretending to a ten-year window while
    silently replaying eighteen months would be the dishonest option. A caller
    that wants more history opens the stock page, which is what fetches it.
    """
    usable = traditional.usable_rows(rows)
    if not usable:
        raise NotEnoughBars("No daily bars stored for this stock")

    dates = [row.date for row in usable]
    closes = {row.date: float(row.close) for row in usable}
    start, end = dates[0], dates[-1]
    start_close, end_close = closes[start], closes[end]

    years = _span_years(start, end)
    if years < MIN_YEARS or len(usable) < years * MIN_BARS_PER_YEAR:
        raise NotEnoughBars(
            f"Need about {MIN_YEARS:g} years of daily bars to judge a holding; "
            f"{len(usable)} session(s) spanning {years:.1f} year(s) stored"
        )

    payouts = _payouts(events, start, end)
    by_date: dict[datetime.date, _Payout] = {p.date: p for p in payouts}

    # One share at the first close, then follow what the company hands over.
    shares = 1.0
    cash_collected = 0.0
    curve: list[HoldEquityPoint] = []

    for day in dates:
        payout = by_date.get(day)
        if payout is not None:
            price = closes[day]
            if payout.cash > 0:
                received = shares * payout.cash
                cash_collected += received
                # Reinvested at the ex-date close. A holder cannot buy before
                # the price has dropped, and the cash does not actually arrive
                # for weeks -- this is the conventional simplification, and it
                # is mildly optimistic.
                if price > 0:
                    shares += received / price
            if payout.stock > 0:
                shares += shares * payout.stock / FACE_VALUE

        curve.append(
            HoldEquityPoint(
                date=day,
                price=round(closes[day] / start_close * 100, 2),
                total=round(shares * closes[day] / start_close * 100, 2),
            )
        )

    price_return = end_close / start_close - 1
    total_return = shares * end_close / start_close - 1

    annualised = (
        round(((1 + total_return) ** (1 / years) - 1) * 100, 2)
        if years >= 1 and total_return > -1
        else None
    )

    # Yield on cost uses the *last* twelve months of payouts against the
    # original price -- the number that keeps rising for a holder whose cost
    # never moves, which is the whole appeal of the method.
    recent_cash = sum(
        p.cash for p in payouts if p.date > end - datetime.timedelta(days=365)
    )

    index_return = (
        _index_return(index_rows, start, end) if index_rows else None
    )

    return HoldBacktestResponse(
        sid=sid,
        name=name,
        start=start,
        end=end,
        years=round(years, 2),
        bars=len(usable),
        start_close=round(start_close, 2),
        end_close=round(end_close, 2),
        price_return_pct=round(price_return * 100, 2),
        total_return_pct=round(total_return * 100, 2),
        dividend_return_pct=round((total_return - price_return) * 100, 2),
        annualised_return_pct=annualised,
        index_sid=index_sid if index_return is not None else None,
        index_return_pct=index_return,
        # Against the *price* return, because the index excludes dividends.
        excess_price_return_pp=(
            round(price_return * 100 - index_return, 2)
            if index_return is not None
            else None
        ),
        cash_collected=round(cash_collected, 4),
        yield_on_cost_pct=(
            round(recent_cash / start_close * 100, 2) if start_close else None
        ),
        max_drawdown_pct=_max_drawdown([p.total for p in curve]),
        fill=_fill_stats(closes, dates, payouts),
        stock_dividend_years=len({p.date.year for p in payouts if p.stock > 0}),
        dividend_coverage=coverage,
        curve=_thin(curve, MAX_CURVE_POINTS),
    )
