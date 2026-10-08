"""Pins the two ways a score-versus-outcome study can lie.

Both failures look like a result:

  * Scoring on the last stored bar, or on today's PE, lets a filing or a
    rally that had not happened yet decide which band a name goes in. The
    forward return then "confirms" a score that already knew it.
  * A median over whichever names have bars is a median over what someone
    opened. The backfill job exists so the sample can be defined; publishing
    before it finishes throws that away.

The thresholds themselves are not under test here. A band that beats another
in this fixture only proves the buckets stayed separate.
"""

from __future__ import annotations

import datetime

from app.models import DailyPrice, DividendEvent, FundamentalsAnnual
from app.services.analysis import score_study
from app.services.analysis.score_study import StockInput

AS_OF = datetime.date(2018, 6, 15)
HORIZON = 5
START = datetime.date(2016, 1, 1)
END = datetime.date(2023, 6, 15)
PAID_YEARS = range(2010, 2018)
EPS_YEARS = range(2013, 2018)


def _days(start: datetime.date, end: datetime.date):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def _bars(
    sid: str,
    *,
    until: datetime.date,
    price_at,
    capacity: int = 1_000_000,
) -> list[DailyPrice]:
    rows = []
    for day in _days(START, until):
        price = float(price_at(day))
        rows.append(
            DailyPrice(
                sid=sid,
                date=day,
                open=price,
                high=price,
                low=price,
                close=price,
                capacity=capacity,
                turnover=int(price * capacity),
            )
        )
    return rows


def _flat(sid: str, *, before: float, after: float, until: datetime.date = END, capacity: int = 1_000_000):
    return _bars(
        sid,
        until=until,
        capacity=capacity,
        price_at=lambda day: before if day <= AS_OF else after,
    )


def _cash(sid: str, years, amount: float = 3.0) -> list[DividendEvent]:
    return [
        DividendEvent(
            sid=sid,
            ex_date=datetime.date(year, 8, 1),
            name="test",
            kind="息",
            cash_dividend=amount,
        )
        for year in years
    ]


def _annual(sid: str, years, *, eps: float, roe: float) -> list[FundamentalsAnnual]:
    return [
        FundamentalsAnnual(sid=sid, year=year, eps=eps, roe=roe, source="test")
        for year in years
    ]


def _strong(
    sid: str,
    *,
    after: float = 40.0,
    until: datetime.date = END,
    dividends: list[DividendEvent] | None = None,
    fundamentals: list[FundamentalsAnnual] | None = None,
) -> StockInput:
    return StockInput(
        sid=sid,
        name=sid,
        industry="半導體",
        benchmark_sid="t00",
        prices=_flat(sid, before=40.0, after=after, until=until),
        dividends=dividends if dividends is not None else _cash(sid, PAID_YEARS),
        fundamentals=(
            fundamentals
            if fundamentals is not None
            else _annual(sid, EPS_YEARS, eps=5.0, roe=15.0)
        ),
        coverage="history",
        observed_years=set(PAID_YEARS),
    )


def _avoid(sid: str) -> StockInput:
    """Fails every dimension on data that was already knowable, on purpose.

    Latest EPS stays positive so Cheap is a high PE rather than `unknown` --
    an unknown dimension would drop the name out of the bands entirely, and
    this fixture is here to land in `avoid`.
    """
    fundamentals = _annual(sid, range(2013, 2017), eps=-1.0, roe=1.0)
    fundamentals.append(
        FundamentalsAnnual(sid=sid, year=2017, eps=1.0, roe=1.0, source="test")
    )
    return StockInput(
        sid=sid,
        name=sid,
        industry="半導體",
        benchmark_sid="t00",
        prices=_flat(sid, before=100.0, after=80.0, capacity=100),
        dividends=_cash(sid, (2017,), amount=1.0),
        fundamentals=fundamentals,
        coverage="history",
        observed_years=set(PAID_YEARS),
    )


def _index(until: datetime.date = END) -> list[DailyPrice]:
    return _bars("t00", until=until, price_at=lambda _day: 1000.0, capacity=1_000_000)


def _run(stocks: list[StockInput], *, as_of: datetime.date = AS_OF):
    return score_study.run(
        stocks,
        as_of=as_of,
        horizon_years=HORIZON,
        indexes={"t00": _index()},
    )


def _band(study, suitability: str):
    return next(band for band in study.bands if band.suitability == suitability)


def test_a_leap_day_horizon_does_not_raise():
    assert score_study.add_years(datetime.date(2020, 2, 29), 1) == datetime.date(2021, 2, 28)
    assert score_study.add_years(AS_OF, HORIZON) == END


def test_nothing_after_the_decision_date_moves_the_score():
    """A later rally, a later filing and a later payout are the outcome.

    Each one, if it leaked, would change a different field: the rally the PE,
    the filing which year is 'latest', the payout the streak. Asserting the
    score stays `strong` is not enough -- a PE of 0.4 and a PE of 8 can both
    pass Cheap.
    """
    stock = _strong("2330", after=400.0)
    stock.dividends.append(
        DividendEvent(
            sid="2330",
            ex_date=datetime.date(2019, 8, 1),
            name="test",
            kind="息",
            cash_dividend=50.0,
        )
    )
    stock.fundamentals.append(
        FundamentalsAnnual(sid="2330", year=2020, eps=100.0, roe=80.0, source="test")
    )

    features, rules = score_study.score_snapshot(stock, AS_OF)

    assert rules.suitability == "strong"
    assert rules.known_weight == 100
    assert features.fundamentals.trailing_pe == 8.0
    assert features.fundamentals.latest_eps_year == 2017
    assert features.dividend.consecutive_years_with_cash == 8
    assert features.price.latest_close == 40.0


def test_a_payout_after_the_decision_still_counts_in_the_return():
    """The same event the score must ignore is the holder's actual cash."""
    stocks = []
    for i in range(score_study.MIN_BUCKET):
        sid = f"23{i:02d}"
        stock = _strong(sid, after=40.0)
        stock.dividends.append(
            DividendEvent(
                sid=sid,
                ex_date=datetime.date(2019, 8, 1),
                name="test",
                kind="息",
                cash_dividend=10.0,
            )
        )
        stocks.append(stock)

    band = _band(_run(stocks), "strong")

    # Flat price, so a total return this far above zero is the payout being
    # reinvested. 10 on a 40 entry is 25% before the new shares are marked.
    assert band.count == score_study.MIN_BUCKET
    assert band.median_total_return_pct is not None
    assert band.median_total_return_pct > 20


def test_bands_keep_their_own_outcomes():
    stocks = [_strong(f"11{i:02d}", after=60.0) for i in range(score_study.MIN_BUCKET)]
    stocks += [_avoid(f"22{i:02d}") for i in range(score_study.MIN_BUCKET)]

    study = _run(stocks)
    strong = _band(study, "strong")
    avoid = _band(study, "avoid")

    assert strong.count == score_study.MIN_BUCKET
    assert avoid.count == score_study.MIN_BUCKET
    assert strong.median_total_return_pct == 50.0
    assert avoid.median_total_return_pct == -20.0
    assert strong.median_excess_price_return_pp == 50.0
    assert avoid.median_excess_price_return_pp == -20.0
    assert study.excluded_thin_score == 0


def test_a_short_band_reports_its_count_and_withholds_the_median():
    study = _run([_strong(f"11{i:02d}", after=60.0) for i in range(3)])
    band = _band(study, "strong")

    assert band.count == 3
    assert band.median_total_return_pct is None
    assert band.withheld == "bucket_too_small"


def test_a_later_print_does_not_finish_a_window_that_stopped_early():
    """A bar years after the horizon is not evidence the horizon was held."""
    stock = _strong("2330", until=datetime.date(2019, 6, 15))
    stock.prices.append(
        DailyPrice(
            sid="2330",
            date=datetime.date(2024, 6, 15),
            open=40,
            high=40,
            low=40,
            close=40,
            capacity=1_000_000,
            turnover=40_000_000,
        )
    )

    study = _run([stock])

    assert study.excluded_pending == 1
    assert study.excluded_thin_outcome == 0
    assert study.included == 0


def test_a_horizon_that_has_not_elapsed_is_not_a_shorter_result():
    study = _run([_strong("2330", until=datetime.date(2019, 6, 15))])

    assert study.included == 0
    assert study.excluded_pending == 1
    assert _band(study, "strong").count == 0


def test_no_session_near_the_decision_date_is_not_scored():
    study = _run([_strong("2330", until=datetime.date(2018, 5, 1))])

    assert study.excluded_no_entry == 1
    assert study.excluded_pending == 0
    assert study.included == 0


def test_a_missing_dimension_does_not_share_a_band_with_a_full_score():
    stock = _strong("2330", fundamentals=[])
    study = _run([stock])

    assert study.excluded_thin_score == 1
    assert study.included == 0


def test_returns_are_withheld_until_the_backfill_universe_is_complete():
    study = _run([_strong(f"11{i:02d}", after=60.0) for i in range(score_study.MIN_BUCKET)])
    published = score_study.publish(study, universe_size=300, remaining=12)

    assert published.sample_ready is False
    assert published.included == score_study.MIN_BUCKET
    assert published.remaining == 12
    for band in published.bands:
        assert band.median_total_return_pct is None
        assert band.median_excess_price_return_pp is None
        assert band.withheld == "universe_incomplete"
    # The count is how far scoring got. Blanking it would hide the progress
    # the remaining figure is supposed to sit next to.
    assert _band(published, "strong").count == score_study.MIN_BUCKET


def test_a_finished_universe_keeps_the_medians():
    study = _run([_strong(f"11{i:02d}", after=60.0) for i in range(score_study.MIN_BUCKET)])
    published = score_study.publish(study, universe_size=8, remaining=0)

    assert published.sample_ready is True
    assert published.cheap_basis == "close_over_annual_eps"
    assert _band(published, "strong").median_total_return_pct == 50.0
    assert _band(published, "strong").withheld is None


def test_an_empty_universe_is_not_a_finished_one():
    published = score_study.publish(_run([]), universe_size=0, remaining=0)

    assert published.sample_ready is False
    assert _band(published, "strong").withheld == "universe_incomplete"
