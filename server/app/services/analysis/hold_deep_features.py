"""What a deep 存股 verdict is shown beyond the checklist's snapshot.

The 存股 counterpart to `deep_features.py`, and it obeys the same three rules:
pure function of rows already held, no session, no network, no clock. A past
date is replayed by slicing the inputs and nothing else.

What it adds is chosen against what the quick lane already sends, because a
second metered call that shows the model more of the same thing is not worth
anyone's money. `hold_features` hands over aggregates -- average EPS, average
ROE, a payout streak, counts of what was checkable. Three things it cannot say:

**Whether the record is improving or decaying.** An average over ten years is
the same number for a company that doubled its earnings and one that halved
them. A holder buying for the next decade is buying the trend, not the mean, so
the deep lane sends the series.

**Whether the payout is funded.** `hold_prompts` already tells the model that a
checklist cannot see a dividend paid out of borrowing. It cannot see it because
nothing in the quick features relates cash paid to earnings made; a payout ratio
per year, and one over the window, is the cheapest thing that can.

**Whether today's price is cheap for this company.** The checklist tests a PE
against a fixed ceiling, which is the half of 買得便宜 a sector constant can
express. The other half is the stock's own band, and `valuation_day` has been
accumulating one session a day for exactly this.

Institutional flow comes along too, from `deep_features` unchanged. A holder
reads it for a different question than a trader does -- whether the accounts
that hold this for years are adding or leaving -- but it is the same
measurement, and re-deriving it here would let the two panels disagree about a
streak over identical rows.

**Coverage is an output, not an absence** -- the rule this module shares with
every other feature extractor in the package. `valuation_day` starts filling on
the day the deployment first ran the job, so an install that is two weeks old
has a two-week "band". Reporting that as a percentile with no qualifier would
hand the model a number that looks like a decade and is a fortnight.
"""

from __future__ import annotations

import datetime

from app.models import (
    ChipDay,
    DailyPrice,
    DividendEvent,
    FundamentalsAnnual,
    ValuationDay,
)
from app.schemas import (
    DeepChipFeatures,
    HoldDeepFundamentals,
    HoldDeepInputs,
    HoldDeepValuation,
    HoldDeepYear,
)
from app.services.analysis import deep_features, hold_features

#: Calendar years of record the series covers. The checklist's own window, so
#: the two lanes are talking about the same decade.
WINDOW_YEARS = hold_features.WINDOW_YEARS

#: Sessions of chip history, from the technical deep lane. Same window for the
#: same reason it is 20 there: long enough for a 買超 run to be a position, and
#: inside what the nightly job keeps warm.
CHIP_WINDOW = deep_features.CHIP_WINDOW

#: Sessions of valuation history the band is read over. Two years, matching
#: `hold_features.PRICE_WINDOW`: "cheap" for a holder is a question about the
#: cycle, and the price position and the PE position should span the same one.
VALUATION_WINDOW = hold_features.PRICE_WINDOW

#: Below this many stored sessions the band is called short. A quarter's
#: trading, the same floor `hold_features.LIQUIDITY_WINDOW` uses: fewer than
#: that and a percentile describes one market mood rather than a range.
VALUATION_MIN_COVERAGE = 60

#: Years of EPS below which the earnings read is flagged short. Same threshold
#: as the technical deep lane, and the same reasoning as `chen_rules`: one or
#: two years is a snapshot, not a record.
MIN_EPS_YEARS = deep_features.MIN_EPS_YEARS


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _percentile(history: list[float], value: float | None) -> float | None:
    """Share of the stored sessions that sat below `value`, 0-100.

    Below rather than at-or-below, so a figure that has not moved in a month
    does not read as expensive because it equals itself thirty times. None when
    there is nothing to rank against -- an empty band is not a median one.
    """
    if value is None or not history:
        return None
    below = sum(1 for v in history if v < value)
    return round(below / len(history) * 100, 1)


def _eps_by_year(rows: list[FundamentalsAnnual]) -> dict[int, float]:
    return {r.year: float(r.eps) for r in rows if r.eps is not None}


def _roe_by_year(rows: list[FundamentalsAnnual]) -> dict[int, float]:
    return {r.year: float(r.roe) for r in rows if r.roe is not None}


def _payout_ratio(cash: float | None, prior_eps: float | None) -> float | None:
    """This year's cash against last fiscal year's earnings.

    Undefined rather than negative when the company lost money: a payout ratio
    of -180% reads as a number and means "paid a dividend it did not earn",
    which is a sentence the risks section should carry instead.
    """
    if cash is None or prior_eps is None or prior_eps <= 0:
        return None
    return round(cash / prior_eps * 100, 1)


def _series(
    as_of: datetime.date,
    eps: dict[int, float],
    roe: dict[int, float],
    cash: dict[int, float],
) -> list[HoldDeepYear]:
    """The window's years, newest first, with the empty ones left out.

    The window ends at the current year only once that year has something in
    it, and at the year before otherwise -- the rule `hold_features._judged_years`
    applies to payout streaks, for the same reason. A company that pays in
    August has not stopped paying when the question is asked in March, and a
    window that spent its newest slot on an empty current year would report
    nine years of record where ten exist.
    """
    known = set(eps) | set(roe) | set(cash)
    last = as_of.year if as_of.year in known else as_of.year - 1
    first = last - WINDOW_YEARS + 1
    years = sorted({y for y in known if first <= y <= last}, reverse=True)
    return [
        HoldDeepYear(
            year=year,
            eps=_round(eps.get(year)),
            roe_pct=_round(roe.get(year)),
            cash_dividend=_round(cash.get(year)),
            # Against the *previous* fiscal year, which is when the cash paid
            # this year was earned. `eps` is the unwindowed map on purpose: the
            # oldest year in the window still has a prior year to divide by.
            payout_ratio_pct=_payout_ratio(cash.get(year), eps.get(year - 1)),
        )
        for year in years
    ]


def _eps_cagr(series: list[HoldDeepYear]) -> float | None:
    """Compound annual growth between the window's first and last earning years.

    Both ends have to be positive. A growth rate across a sign change is a real
    number the arithmetic will happily produce and nobody can interpret, and
    this lane's whole risk is a produced number being read as a fact.
    """
    earning = [y for y in series if y.eps is not None]
    if len(earning) < 2:
        return None
    newest, oldest = earning[0], earning[-1]
    span = newest.year - oldest.year
    if span < 1 or oldest.eps is None or newest.eps is None:
        return None
    if oldest.eps <= 0 or newest.eps <= 0:
        return None
    return round(((newest.eps / oldest.eps) ** (1 / span) - 1) * 100, 2)


def _eps_down_years(series: list[HoldDeepYear]) -> int | None:
    """Years EPS fell against the one before, where both are known.

    Defined where a CAGR is not -- a loss-making year has a direction even when
    it has no growth rate -- which is why both are reported.
    """
    eps = {y.year: y.eps for y in series if y.eps is not None}
    pairs = [
        (value, prior)
        for year, value in eps.items()
        if (prior := eps.get(year - 1)) is not None
    ]
    # No consecutive pair to compare. Returning 0 here would read as "earnings
    # never fell" off two figures four years apart, which is the "we did not
    # look" -> "we looked and it was zero" failure this package exists to
    # avoid everywhere else.
    if not pairs:
        return None
    return sum(1 for value, prior in pairs if value < prior)


def _avg_payout_ratio(
    series: list[HoldDeepYear], eps: dict[int, float]
) -> float | None:
    """Total cash paid over the window against the earnings it came out of.

    Summed rather than averaged over the per-year ratios, and that is why both
    are reported: a quarterly payer's distribution of one fiscal year lands in
    two calendar ones, so each yearly ratio is a little wrong in a direction
    that cancels inside a sum. Only pairs where both halves are known count, or
    a year whose payout was never fetched would read as a year nothing was paid.
    """
    paid = 0.0
    earned = 0.0
    for year in series:
        prior = eps.get(year.year - 1)
        if year.cash_dividend is None or prior is None or prior <= 0:
            continue
        paid += year.cash_dividend
        earned += prior
    if earned <= 0:
        return None
    return round(paid / earned * 100, 1)


def _fundamentals(
    as_of: datetime.date,
    annual: list[FundamentalsAnnual],
    dividends: list[DividendEvent],
) -> HoldDeepFundamentals:
    eps = _eps_by_year(annual)
    roe = _roe_by_year(annual)
    # Bounded at `as_of` for the reason `hold_features` bounds it: a snapshot
    # replayed for a past date must not see a payout that had not happened yet.
    cash = hold_features.cash_by_year([e for e in dividends if e.ex_date <= as_of])

    series = _series(as_of, eps, roe, cash)
    return HoldDeepFundamentals(
        years=series,
        eps_down_years=_eps_down_years(series),
        eps_cagr_pct=_eps_cagr(series),
        avg_payout_ratio_pct=_avg_payout_ratio(series, eps),
        years_with_eps=sum(1 for y in series if y.eps is not None),
        years_with_dividend=sum(1 for y in series if y.cash_dividend is not None),
    )


def _valuation(rows: list[ValuationDay]) -> HoldDeepValuation:
    """Today's PE, PB and yield, and where each sits in this stock's own band."""
    sessions = sorted(rows, key=lambda r: r.date, reverse=True)[:VALUATION_WINDOW]
    if not sessions:
        return HoldDeepValuation(
            days_covered=0,
            first_date=None,
            last_date=None,
            pe_ratio=None,
            pe_percentile=None,
            pb_ratio=None,
            pb_percentile=None,
            dividend_yield_pct=None,
            dividend_yield_percentile=None,
        )

    def _history(attr: str) -> list[float]:
        return [
            float(getattr(r, attr)) for r in sessions if getattr(r, attr) is not None
        ]

    def _current(attr: str) -> float | None:
        # Newest non-null rather than the newest row's value: the exchange
        # leaves PE blank for a company with no positive trailing earnings, and
        # one such session must not erase a band that is otherwise complete.
        for row in sessions:
            value = getattr(row, attr)
            if value is not None:
                return float(value)
        return None

    pe, pb, dy = _current("pe_ratio"), _current("pb_ratio"), _current("dividend_yield")
    return HoldDeepValuation(
        days_covered=len(sessions),
        first_date=sessions[-1].date,
        last_date=sessions[0].date,
        pe_ratio=_round(pe),
        pe_percentile=_percentile(_history("pe_ratio"), pe),
        pb_ratio=_round(pb),
        pb_percentile=_percentile(_history("pb_ratio"), pb),
        dividend_yield_pct=_round(dy),
        dividend_yield_percentile=_percentile(_history("dividend_yield"), dy),
    )


def _coverage_gaps(
    fundamentals: HoldDeepFundamentals,
    valuation: HoldDeepValuation,
    chip: DeepChipFeatures,
) -> list[str]:
    """What the deep read could not see, as the slugs every panel already renders.

    Reuses `deep_features.chip_gaps` and the checklist's fundamentals and
    dividend slugs wherever the condition is the same one. Only the valuation
    band needs new identifiers, because it is the one input no other lane has.
    """
    gaps: list[str] = deep_features.chip_gaps(chip)

    if fundamentals.years_with_eps == 0:
        gaps.append("no_annual_fundamentals")
    elif fundamentals.years_with_eps < MIN_EPS_YEARS:
        gaps.append("short_eps_history")

    if fundamentals.years_with_dividend == 0:
        gaps.append("no_dividend_history")

    if valuation.days_covered == 0:
        gaps.append("no_valuation_history")
    elif valuation.days_covered < VALUATION_MIN_COVERAGE:
        # A distinct answer from having none: a fortnight of sessions produces
        # a percentile that looks exactly like a decade's and means nothing
        # like it, and the prompt caps its confidence on this slug.
        gaps.append("short_valuation_history")

    return gaps


def extract(
    *,
    as_of: datetime.date,
    prices: list[DailyPrice],
    chips: list[ChipDay],
    fundamentals: list[FundamentalsAnnual],
    dividends: list[DividendEvent],
    valuations: list[ValuationDay],
) -> HoldDeepInputs:
    """The deep 存股 prompt's extra inputs, plus what is missing.

    Never raises and never returns None, exactly as `deep_features.extract`
    does not. A company with no annual figures, no payout archive and no chip
    coverage still produces a structure whose `coverage_gaps` say the deep read
    found nothing the checklist did not -- a worse answer, and an answer, which
    is the point: refusing to generate would leave the button dead on every
    stock nobody has warmed yet.
    """
    chip = deep_features.chip_features(chips, prices)
    annual = _fundamentals(as_of, fundamentals, dividends)
    valuation = _valuation(valuations)
    return HoldDeepInputs(
        fundamentals=annual,
        valuation=valuation,
        chip=chip,
        coverage_gaps=_coverage_gaps(annual, valuation, chip),
    )
