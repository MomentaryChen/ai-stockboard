"""Does a high 存股 score predict a better holding outcome?

The checklist encodes published thresholds -- ROE, PE, an eight-year streak,
the weights -- and nothing in the product had checked whether a name that
clears them then did better than one that does not. The hold backtest made
one company's outcome computable. This buckets a whole universe by the score
it would have received on one past date, and reports what the following years
returned.

Two refusals are the point of having it as its own module.

**The score may only see what was knowable that day.** Bars, ex-dates and
annual filings after the decision date are the outcome, not the evidence. The
live card's trailing PE is the exchange's daily figure, and `valuation_day`
holds the latest one: feeding it in here would score 2018 with a 2026
multiple. Cheap is therefore close divided by the annual EPS already on file,
which is the checklist's own fallback and the only PE this study can compute
without look-ahead.

**A partial universe is not a result.** `history_backfill` exists because
`daily_price` otherwise holds whichever names someone opened. Publishing
medians over that set answers "what did the browsed names do", which is a
different question, so `publish` drops every return until that job reports
nothing left.
"""

from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailyPrice, DividendEvent, FundamentalsAnnual
from app.schemas import HoldSuitability, ScoreStudyBand, ScoreStudyResponse
from app.services import codes as codes_service
from app.services import dividend as dividend_service
from app.services import history as history_service
from app.services import history_backfill
from app.services import market_index
from app.services.analysis import chen_rules, hold_backtest, hold_features, traditional

#: How long a scored name is then held. Five years is the horizon the method
#: is about; shorter and one cycle dominates, longer and a ten-year price
#: store has nothing left to score *from*.
DEFAULT_HORIZON_YEARS = 5

#: Below this many names a median is one or two stocks wearing a band's
#: authority. The count is still reported -- "only three names scored avoid"
#: is a fact about coverage, not a return.
MIN_BUCKET = 8

#: A decision date with no session this close has no price a holder could have
#: paid. Scoring last month's close as if it were today's is a different study.
ENTRY_LAG_DAYS = 14

#: The outcome window has to actually elapse. A name whose bars stop a year
#: early -- delisted, or never fetched that far -- is left out rather than
#: graded over a shorter life than its neighbours. That exclusion is survivor
#: bias, and the response counts it instead of hiding it inside the median.
PENDING_LAG_DAYS = 14

CHEAP_BASIS = "close_over_annual_eps"

BANDS: tuple[HoldSuitability, ...] = ("strong", "ok", "weak", "avoid")

settings = get_settings()


class NoEntry(RuntimeError):
    """No session close enough to the decision date to score or to buy at."""


@dataclass(frozen=True, slots=True)
class StockInput:
    """One company's stored rows. The study slices them; it does not fetch."""

    sid: str
    name: str
    industry: str
    benchmark_sid: str
    prices: list[DailyPrice]
    dividends: list[DividendEvent]
    fundamentals: list[FundamentalsAnnual]
    coverage: str
    observed_years: set[int]


@dataclass(frozen=True, slots=True)
class _Outcome:
    suitability: HoldSuitability
    total_return_pct: float
    annualised_return_pct: float | None
    excess_price_return_pp: float | None
    max_drawdown_pct: float


@dataclass
class Study:
    as_of: datetime.date
    horizon_years: int
    horizon_end: datetime.date
    included: int = 0
    excluded_no_entry: int = 0
    excluded_pending: int = 0
    excluded_thin_outcome: int = 0
    excluded_thin_score: int = 0
    bands: list[ScoreStudyBand] = field(default_factory=list)


def add_years(day: datetime.date, years: int) -> datetime.date:
    """Calendar years, with Feb 29 landing on Feb 28.

    `years` may be negative. A horizon measured in bars would shrink for a
    name that did not trade, which is the distortion `_span_years` already
    refuses next door.
    """
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, month=2, day=28)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 2)


def score_snapshot(stock: StockInput, as_of: datetime.date):
    """Checklist as of the last session on or before `as_of`.

    Everything after that session is dropped before the extractors run, so a
    later filing cannot move the score even if an extractor forgot its own
    year cap. Valuation is not an argument: there is no historical PE to pass.
    """
    # Sort before taking the last session. `usable_rows` keeps input order,
    # and a caller that loaded prices grouped by something other than date
    # would otherwise score the wrong close.
    ordered = sorted(stock.prices, key=lambda row: row.date)
    usable = [row for row in traditional.usable_rows(ordered) if row.date <= as_of]
    if not usable or (as_of - usable[-1].date).days > ENTRY_LAG_DAYS:
        raise NoEntry(stock.sid)
    clock = usable[-1].date

    prices = [row for row in ordered if row.date <= clock]
    dividends = [event for event in stock.dividends if event.ex_date <= clock]
    year_cap = clock.year - 1
    fundamentals = [row for row in stock.fundamentals if row.year <= year_cap]
    observed = {year for year in stock.observed_years if year <= clock.year}

    features = hold_features.extract(
        sid=stock.sid,
        name=stock.name,
        industry=stock.industry,
        prices=prices,
        dividends=dividends,
        coverage=stock.coverage,
        observed_dividend_years=observed,
        fundamentals=fundamentals,
    )
    return features, chen_rules.evaluate(features)


def _classify(
    stock: StockInput,
    as_of: datetime.date,
    horizon_end: datetime.date,
    indexes: dict[str, list[DailyPrice]],
) -> tuple[str, _Outcome | None]:
    prices = sorted(stock.prices, key=lambda row: row.date)
    usable = traditional.usable_rows(prices)
    if not usable:
        return "no_entry", None

    # Entry first. A series that never traded near the decision date was not
    # a candidate, even when it also fails the horizon check.
    prior = [row for row in usable if row.date <= as_of]
    if not prior or (as_of - prior[-1].date).days > ENTRY_LAG_DAYS:
        return "no_entry", None
    entry = prior[-1]

    # The series has to *reach* the horizon, not merely have some later bar.
    # A name with a hole through the end of the window and a print years
    # afterward would otherwise be graded on the short stretch before the
    # hole -- a different length of history from every name that traded
    # through.
    through = [row for row in usable if row.date <= horizon_end]
    if not through or (horizon_end - through[-1].date).days > PENDING_LAG_DAYS:
        return "pending", None

    window = [row for row in prices if entry.date <= row.date <= horizon_end]
    try:
        replay = hold_backtest.run(
            stock.sid,
            stock.name,
            window,
            stock.dividends,
            coverage=stock.coverage,
            index_rows=indexes.get(stock.benchmark_sid, []),
            index_sid=stock.benchmark_sid,
        )
    except hold_backtest.NotEnoughBars:
        return "thin_outcome", None

    _features, rules = score_snapshot(stock, as_of)
    # A dimension left unknown shrinks the denominator. 100 over 35 weight
    # and 80 over 100 are not the same claim, and mixing them is how a thin
    # name outranks a fully scored one.
    if (
        rules.suitability is None
        or rules.known_weight < chen_rules.TOTAL_WEIGHT
    ):
        return "thin_score", None

    return "included", _Outcome(
        suitability=rules.suitability,
        total_return_pct=replay.total_return_pct,
        annualised_return_pct=replay.annualised_return_pct,
        excess_price_return_pp=replay.excess_price_return_pp,
        max_drawdown_pct=replay.max_drawdown_pct,
    )


def _band(suitability: HoldSuitability, rows: list[_Outcome]) -> ScoreStudyBand:
    enough = len(rows) >= MIN_BUCKET
    annualised = [
        row.annualised_return_pct
        for row in rows
        if row.annualised_return_pct is not None
    ]
    excess = [
        row.excess_price_return_pp
        for row in rows
        if row.excess_price_return_pp is not None
    ]
    return ScoreStudyBand(
        suitability=suitability,
        count=len(rows),
        median_total_return_pct=(
            _median([row.total_return_pct for row in rows]) if enough else None
        ),
        median_annualised_return_pct=(
            _median(annualised) if enough and len(annualised) >= MIN_BUCKET else None
        ),
        median_excess_price_return_pp=(
            _median(excess) if enough and len(excess) >= MIN_BUCKET else None
        ),
        median_max_drawdown_pct=(
            _median([row.max_drawdown_pct for row in rows]) if enough else None
        ),
        withheld=None if enough else "bucket_too_small",
    )


def run(
    stocks: list[StockInput],
    *,
    as_of: datetime.date,
    horizon_years: int,
    indexes: dict[str, list[DailyPrice]],
) -> Study:
    """Bucket `stocks` by the score on `as_of` and the return through the horizon.

    Always computes. Whether those returns may be shown is `publish`'s
    decision, because this function cannot see whether the caller handed it
    the backfill universe or a watchlist.
    """
    horizon_end = add_years(as_of, horizon_years)
    grouped: dict[HoldSuitability, list[_Outcome]] = {band: [] for band in BANDS}
    study = Study(as_of=as_of, horizon_years=horizon_years, horizon_end=horizon_end)

    for stock in stocks:
        kind, outcome = _classify(stock, as_of, horizon_end, indexes)
        if kind == "no_entry":
            study.excluded_no_entry += 1
        elif kind == "pending":
            study.excluded_pending += 1
        elif kind == "thin_outcome":
            study.excluded_thin_outcome += 1
        elif kind == "thin_score":
            study.excluded_thin_score += 1
        elif outcome is not None:
            study.included += 1
            grouped[outcome.suitability].append(outcome)

    study.bands = [_band(band, grouped[band]) for band in BANDS]
    return study


def publish(study: Study, *, universe_size: int, remaining: int) -> ScoreStudyResponse:
    """Blank every return unless the backfill universe is complete and non-empty.

    Counts stay. An operator watching the job needs to see that names are
    scoreable; what they must not be able to quote is a median.
    """
    ready = universe_size > 0 and remaining == 0
    bands = study.bands
    if not ready:
        bands = [
            band.model_copy(
                update={
                    "median_total_return_pct": None,
                    "median_annualised_return_pct": None,
                    "median_excess_price_return_pp": None,
                    "median_max_drawdown_pct": None,
                    "withheld": "universe_incomplete",
                }
            )
            for band in bands
        ]
    return ScoreStudyResponse(
        as_of=study.as_of,
        horizon_years=study.horizon_years,
        horizon_end=study.horizon_end,
        cheap_basis=CHEAP_BASIS,
        sample_ready=ready,
        universe_size=universe_size,
        remaining=remaining,
        included=study.included,
        excluded_no_entry=study.excluded_no_entry,
        excluded_pending=study.excluded_pending,
        excluded_thin_outcome=study.excluded_thin_outcome,
        excluded_thin_score=study.excluded_thin_score,
        min_bucket=MIN_BUCKET,
        bands=bands,
    )


def _rows_by_sid(rows) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.sid, []).append(row)
    return grouped


def _load_rows(db: Session, model, sids: list[str]) -> dict[str, list]:
    if not sids:
        return {}
    rows = db.execute(select(model).where(model.sid.in_(sids))).scalars()
    return _rows_by_sid(rows)


def load(
    db: Session,
    *,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    as_of: datetime.date | None = None,
) -> ScoreStudyResponse:
    """Run the study over the backfill universe and withhold it if unfinished.

    Cache-only. The page that shows this is an admin screen, and a cold name
    in the universe must not spend the exchange budget the realtime poll shares
    -- that is what the backfill job is for.
    """
    covered = history_backfill.coverage(db)
    start, _expected = history_backfill.expected_window(settings.history_backfill_years)

    indexes = {
        sid: history_service.read_prices(db, sid, start)
        for sid in (market_index.DEFAULT_INDEX, market_index.TPEX_INDEX)
    }
    if as_of is None:
        # The index's last stored session is "now" for a cache-only study.
        # Wall-clock today would ask for a horizon that ends in the future
        # whenever the latest bar is yesterday, and every name would look pending.
        usable = traditional.usable_rows(indexes[market_index.DEFAULT_INDEX])
        anchor = usable[-1].date if usable else datetime.date.today()
        as_of = add_years(anchor, -horizon_years)

    sids = list(covered.sids)
    prices = history_service.read_prices_many(db, sids, start)
    dividends = _load_rows(db, DividendEvent, sids)
    fundamentals = _load_rows(db, FundamentalsAnnual, sids)
    # One board-wide stamp, not a per-name fetch. `score_snapshot` drops years
    # after the decision date; a year still in the set only means the archive
    # for that year was pulled, which is what Collect uses to tell "we have
    # not looked" from "they skipped a payout".
    observed = dividend_service.observed_years(
        db, dividend_service.year_range(hold_features.WINDOW_YEARS + 1)
    )

    stocks: list[StockInput] = []
    for sid in sids:
        info = codes_service.get_stock(sid)
        if info is None or market_index.is_index(sid):
            continue
        source = info.data_source
        stocks.append(
            StockInput(
                sid=sid,
                name=info.name,
                industry=info.group,
                benchmark_sid=market_index.benchmark_for(source),
                prices=prices.get(sid, []),
                dividends=dividends.get(sid, []),
                fundamentals=fundamentals.get(sid, []),
                coverage="recent" if source == "tpex" else "history",
                observed_years=set() if source == "tpex" else set(observed),
            )
        )

    study = run(stocks, as_of=as_of, horizon_years=horizon_years, indexes=indexes)
    return publish(
        study, universe_size=len(covered.sids), remaining=covered.remaining
    )
