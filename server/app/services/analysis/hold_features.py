"""Derived facts about a company's dividend record, liquidity and earnings.

The 存股 counterpart to `features.py`, and it exists for the same reason: the
arithmetic is done here so that neither the rule engine below it nor the model
beside it has to add anything up. Everything is a pure function of rows already
stored -- `dividend_event`, `daily_price`, `fundamentals_annual` -- with no
session, no network and no clock, so a snapshot for a past date is produced by
slicing the inputs and nothing else.

Two things are deliberately different from the technical feature set.

**Coverage is part of the output.** A yield computed from TPEX's current-window
snapshot and a yield computed from ten years of TWSE archive are the same
number carrying very different weight, so `coverage` and the various
`*_years_checked` counters travel with the figures they qualify. The checklist
downstream turns a missing count into `unknown` rather than into a zero.

**The window is years, not days.** Which means the current calendar year is
usually incomplete, and every streak here excludes it: a company that pays in
August has not stopped paying when the question is asked in March, and a rule
that counted it as a break would fail every good name for eight months of the
year.
"""

from __future__ import annotations

import datetime
import statistics

from app.models import DailyPrice, DividendEvent, FundamentalsAnnual
from app.schemas import (
    HoldDividendFeatures,
    HoldFeatures,
    HoldFundamentalsFeatures,
    HoldLiquidityFeatures,
    HoldPriceFeatures,
)
from app.services.analysis import traditional

#: How far back the checklist looks. Ten years is Chen's own framing ("has it
#: earned every year") and is also roughly the longest TWSE's yearly dividend
#: report can be asked for without the request count getting silly.
WINDOW_YEARS = 10

#: Sessions the liquidity read averages over -- a quarter's trading, the same
#: window `features.RANGE_WINDOW` uses, so "normal volume" means one thing in
#: both lanes.
LIQUIDITY_WINDOW = 60

#: Sessions the long-range price position is read over. Deliberately about two
#: years rather than the technical lane's 60 days: "is it cheap" for a holder
#: is a question about the cycle, not about the last quarter.
PRICE_WINDOW = 480

#: A trading year, for the one-year return. Approximate on purpose -- the exact
#: count varies with the exchange calendar and no decision here turns on it.
TRADING_DAYS_PER_YEAR = 240


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _pct(numerator: float, denominator: float | None) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator * 100, 2)


def _judged_years(as_of: datetime.date, paid_years: set[int]) -> tuple[int, int]:
    """First and last calendar year the continuity rules may look at.

    The current year is normally excluded, because a payment that has not
    happened yet is not a payment that was skipped -- a company paying every
    August would otherwise read as lapsed for eight months of every year.

    It is included once it has actually paid. At that point there is nothing
    left to wait for, and holding it out would make a streak lag its own
    evidence by a year.
    """
    last = as_of.year if as_of.year in paid_years else as_of.year - 1
    return last - WINDOW_YEARS + 1, last


def _cash_by_year(events: list[DividendEvent]) -> dict[int, float]:
    """Total cash paid per calendar year, keyed on the ex-date's year.

    Ex-date rather than the fiscal year it was declared for: this is what a
    holder actually received and when, which is the quantity every rule below
    is about. A company paying twice in one year sums.
    """
    totals: dict[int, float] = {}
    for event in events:
        cash = event.cash_dividend
        if cash is None or float(cash) <= 0:
            continue
        totals[event.ex_date.year] = totals.get(event.ex_date.year, 0.0) + float(cash)
    return totals


def _streak(years_paid: set[int], first: int, last: int) -> int:
    """Unbroken run of paying years ending at `last`, bounded below by `first`."""
    run = 0
    year = last
    while year >= first and year in years_paid:
        run += 1
        year -= 1
    return run


def _ttm_cash(events: list[DividendEvent], as_of: datetime.date) -> float | None:
    """Cash whose ex-date fell in the trailing twelve months.

    Trailing twelve months rather than "this year" because the yield on the
    card has to mean the same thing in January as in December. Upcoming
    ex-dates are excluded -- they are announcements, not cash received.
    """
    window_start = as_of - datetime.timedelta(days=365)
    total = 0.0
    found = False
    for event in events:
        if not (window_start < event.ex_date <= as_of):
            continue
        cash = event.cash_dividend
        if cash is None:
            continue
        total += float(cash)
        found = True
    return round(total, 4) if found else None


def _dividend_features(
    events: list[DividendEvent],
    coverage: str,
    observed: set[int],
    as_of: datetime.date | None,
    latest_close: float | None,
) -> HoldDividendFeatures:
    if as_of is None:
        as_of = max((e.ex_date for e in events), default=datetime.date(1970, 1, 1))

    # Only cash already gone ex counts as paid: an announced ex-date is a
    # promise, and letting it anchor the window would extend a streak on the
    # strength of one.
    by_year = _cash_by_year([e for e in events if e.ex_date <= as_of])
    first_year, last_year = _judged_years(as_of, set(by_year))
    in_window = {y: v for y, v in by_year.items() if first_year <= y <= last_year}

    ttm = _ttm_cash(events, as_of)
    # Averaged over the window's *paying* years rather than over ten: a name
    # listed three years ago has three years of record, and dividing its cash
    # by ten would report it as a poor payer rather than as a young one. The
    # continuity rules above are what catch a company that actually stopped.
    avg_cash = round(statistics.fmean(in_window.values()), 4) if in_window else None

    stock_years = {
        e.ex_date.year
        for e in events
        if e.stock_dividend is not None
        and float(e.stock_dividend) > 0
        and first_year <= e.ex_date.year <= last_year
    }

    past = [e.ex_date for e in events if e.ex_date <= as_of]

    return HoldDividendFeatures(
        coverage=coverage,
        window_years=WINDOW_YEARS,
        years_observed=sum(1 for y in observed if first_year <= y <= last_year),
        years_with_cash=len(in_window),
        consecutive_years_with_cash=_streak(set(in_window), first_year, last_year),
        ttm_cash=ttm,
        cash_yield_pct=_pct(ttm, latest_close) if ttm is not None else None,
        avg_cash_per_year=avg_cash,
        avg_yield_pct=_pct(avg_cash, latest_close) if avg_cash is not None else None,
        latest_ex_date=max(past) if past else None,
        years_with_stock_dividend=len(stock_years),
    )


def _liquidity_features(rows: list[DailyPrice]) -> HoldLiquidityFeatures:
    """Shares and money changing hands, over the recent window.

    Reads the raw rows rather than `usable_rows`: a session that traded nothing
    is exactly what this dimension is looking for, and dropping it would let a
    name that trades one day in three report the average of the days it did.
    """
    window = rows[-LIQUIDITY_WINDOW:]
    shares = [int(r.capacity) for r in window if r.capacity is not None]
    turnover = [int(r.turnover) for r in window if r.turnover is not None]
    return HoldLiquidityFeatures(
        trading_days=len(window),
        avg_daily_shares=int(statistics.fmean(shares)) if shares else None,
        avg_daily_turnover=int(statistics.fmean(turnover)) if turnover else None,
        no_trade_days=sum(
            1 for r in window if r.capacity is None or int(r.capacity) == 0
        ),
    )


def fundamentals_features(
    rows: list[FundamentalsAnnual],
    as_of: datetime.date | None,
    latest_close: float | None,
) -> HoldFundamentalsFeatures:
    """EPS, ROE and the trailing PE drawn from them.

    Returns an all-null structure when `fundamentals_annual` is empty, which is
    the normal state until the ingest lands. It is a distinct shape from "we
    looked and the company lost money", and the checklist relies on the
    difference.

    Public, unlike its siblings here, because the deep technical lane reads the
    same annual figures and asking a different question of them is not a reason
    to derive them a second way -- two extractors over one table drift the
    first time a column is added.
    """
    year_cap = (as_of.year if as_of else datetime.date.today().year) - 1
    window = sorted(
        (r for r in rows if year_cap - WINDOW_YEARS + 1 <= r.year <= year_cap),
        key=lambda r: r.year,
    )

    eps = [(r.year, float(r.eps)) for r in window if r.eps is not None]
    roe = [float(r.roe) for r in window if r.roe is not None]

    latest_year, latest_eps = eps[-1] if eps else (None, None)
    trailing_pe = None
    if latest_close is not None and latest_eps is not None and latest_eps > 0:
        trailing_pe = round(latest_close / latest_eps, 2)

    return HoldFundamentalsFeatures(
        years_available=len(rows),
        eps_years_checked=len(eps),
        eps_positive_years=sum(1 for _, v in eps if v > 0) if eps else None,
        latest_eps=_round(latest_eps, 4),
        latest_eps_year=latest_year,
        avg_eps=_round(statistics.fmean(v for _, v in eps), 4) if eps else None,
        avg_roe_pct=_round(statistics.fmean(roe)) if roe else None,
        # A single year has no spread; reporting 0 would read as "perfectly
        # stable" for the case where stability is unmeasured.
        roe_stdev_pct=_round(statistics.stdev(roe)) if len(roe) >= 2 else None,
        roe_years_checked=len(roe),
        trailing_pe=trailing_pe,
    )


def _price_features(rows: list[DailyPrice]) -> HoldPriceFeatures:
    usable = traditional.usable_rows(rows)
    closes = [float(r.close) for r in usable]
    if not closes:
        return HoldPriceFeatures(
            latest_close=None,
            window_high=None,
            window_low=None,
            position_pct=None,
            drawdown_from_high_pct=None,
            return_1y_pct=None,
        )

    window = closes[-PRICE_WINDOW:]
    latest = window[-1]
    high = max(window)
    low = min(window)

    position = round((latest - low) / (high - low) * 100, 2) if high > low else None
    year_ago = (
        window[-1 - TRADING_DAYS_PER_YEAR] if len(window) > TRADING_DAYS_PER_YEAR else None
    )

    return HoldPriceFeatures(
        latest_close=round(latest, 2),
        window_high=round(high, 2),
        window_low=round(low, 2),
        position_pct=position,
        drawdown_from_high_pct=round((latest - high) / high * 100, 2) if high else None,
        return_1y_pct=(
            round((latest / year_ago - 1) * 100, 2) if year_ago else None
        ),
    )


def extract(
    *,
    sid: str,
    name: str,
    industry: str,
    prices: list[DailyPrice],
    dividends: list[DividendEvent],
    coverage: str,
    observed_dividend_years: set[int],
    fundamentals: list[FundamentalsAnnual],
) -> HoldFeatures:
    """The 存股 snapshot for whatever rows the caller supplies.

    Always returns a structure, never None -- unlike the technical extractor,
    which refuses below 20 bars. There is no floor here because the dimensions
    degrade independently: a name with no price history still has a dividend
    record worth reading, and the checklist marks what it could not score
    rather than declining to answer at all.
    """
    usable = traditional.usable_rows(prices)
    as_of = usable[-1].date if usable else None
    price = _price_features(prices)

    return HoldFeatures(
        sid=sid,
        name=name,
        as_of=as_of,
        industry=industry,
        dividend=_dividend_features(
            dividends, coverage, observed_dividend_years, as_of, price.latest_close
        ),
        liquidity=_liquidity_features(prices),
        fundamentals=fundamentals_features(fundamentals, as_of, price.latest_close),
        price=price,
    )
