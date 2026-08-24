"""Fill in years of daily bars for the names the 存股 study needs.

Everything in this service fetches history lazily: a stock has bars because
somebody opened its page. That is the right default for a dashboard and the
wrong one for a study. It means `daily_price` holds whatever the operator
happened to browse, which is why the hold backtest's window differs per stock
and why "bucket by Chen score and measure what happened next" cannot be run --
the sample would be a record of someone's clicking, not of the market.

This walks a defined universe and fills it deliberately.

## Three constraints, and what each one forces

**The exchange budget is shared with live traffic.** `twse_throttle` is the
same limiter the realtime poll and every page load queue on. A backfill left
to run flat out would not break anything, but it would sit in front of a user
waiting for a quote. So the job takes a *months-per-run* budget rather than
running to completion, and refuses to run at all while the market is open.

That budget is soft, and the difference matters when sizing it: it is checked
between stocks, so a run finishes the name it is on rather than leaving it
half-fetched. The real ceiling is `budget + (years * 12 - 1)` months. Asking
for 12 with a ten-year depth fetches 120, because the first stock alone is
that many -- so the setting is a target for a run, not a promise about one.

**It has to survive being interrupted.** `fetch_log` already stamps every
(sid, year, month) that has landed, so progress is a property of the database
rather than of the run. A killed container resumes from where it stopped, and
`remaining` tells an operator how much is left without doing arithmetic.

**The universe has to be the population the study is about.** Every listed
company is roughly a quarter of a million month-fetches, most of them for
names no dividend strategy would ever consider. Ranking by *paying years*
gives the set Chen's method actually addresses, and it is self-limiting
without anyone picking a market-cap cutoff the service has no data for.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DividendEvent, FetchLog
from app.services import codes as codes_service
from app.services import history as history_service
from app.services.market_open import MARKET_TZ

logger = logging.getLogger(__name__)
settings = get_settings()

#: Taipei trading hours, widened at both ends. The exchange runs 09:00-13:30;
#: the margin covers the pre-open auction and the post-close reports that the
#: chip and index jobs pull. Outside this the shared limiter is idle.
QUIET_AFTER_HOUR = 15
QUIET_BEFORE_HOUR = 8


def is_market_hours(now: datetime.datetime | None = None) -> bool:
    """Whether live traffic is likely competing for the exchange budget.

    Weekday-and-clock only: no holiday calendar. Being wrong on a public
    holiday costs one skipped run out of twenty-four, which is cheaper than
    the alternative error of running through an actual session.
    """
    now = now or datetime.datetime.now(MARKET_TZ)
    if now.weekday() >= 5:
        return False
    return QUIET_BEFORE_HOUR <= now.hour < QUIET_AFTER_HOUR


def universe(db: Session, limit: int, years: int) -> list[str]:
    """Companies with the longest cash-payout record, most consistent first.

    The population the method is about, rather than a market-cap cutoff the
    service has no data for. A company that has paid in nine of the last ten
    years is exactly what a 存股 study wants in its sample; one that has never
    paid contributes nothing to a question about dividend holding.

    Reads `dividend_event`, which the board-wide warmup fills for the whole
    market -- so this needs no per-stock work to decide what to fetch.
    """
    cutoff = datetime.date(datetime.date.today().year - years, 1, 1)
    paying_years = func.count(func.distinct(func.extract("year", DividendEvent.ex_date)))

    rows = db.execute(
        select(DividendEvent.sid, paying_years.label("years"))
        .where(
            DividendEvent.ex_date >= cutoff,
            DividendEvent.cash_dividend.is_not(None),
            DividendEvent.cash_dividend > 0,
        )
        .group_by(DividendEvent.sid)
        .order_by(paying_years.desc(), DividendEvent.sid)
    ).all()

    # Warrants, ETFs and delisted codes are excluded here rather than in SQL:
    # the listing lives in a cached snapshot, not in a table this query can
    # join against.
    companies = set(codes_service.company_codes())
    return [sid for sid, _ in rows if sid in companies][:limit]


def _landed_months(db: Session, sids: list[str], start: datetime.date) -> dict[str, int]:
    """How many month buckets each sid already has, in one query.

    Counted rather than re-derived per stock: asking `ensure_months` to decide
    would mean building the bucket list for every name in the universe just to
    discover most of them are done.
    """
    if not sids:
        return {}
    # Compared as (year, month) rather than as `year * 100 + month`. Both
    # columns are SMALLINT, so the arithmetic version overflows: Postgres binds
    # the literal 100 as a smallint too, and 2026 * 100 is past 32767. It fails
    # loudly, which is lucky -- the same expression in a nullable context could
    # just as easily have gone quiet.
    rows = db.execute(
        select(FetchLog.sid, func.count())
        .where(
            FetchLog.sid.in_(sids),
            or_(
                FetchLog.year > start.year,
                and_(FetchLog.year == start.year, FetchLog.month >= start.month),
            ),
        )
        .group_by(FetchLog.sid)
    ).all()
    return {sid: count for sid, count in rows}


def run(
    db: Session,
    *,
    years: int,
    stocks: int,
    month_budget: int,
    force: bool = False,
) -> dict[str, int]:
    """Fetch up to `month_budget` missing months across the universe.

    Returns before spending anything when the market is open, unless `force` --
    the manual run-now button is an operator saying they know what they are
    doing, and a deliberate run during a lunch break is a reasonable thing to
    want.
    """
    if not force and is_market_hours():
        return {
            "fetched": 0, "stocks": 0, "complete": 0, "remaining": -1, "skipped": 1,
        }

    buckets = history_service.month_range(years * 12)
    start = datetime.date(buckets[0][0], buckets[0][1], 1)
    expected = len(buckets)

    targets = universe(db, stocks, years)
    landed = _landed_months(db, targets, start)

    stats = {"fetched": 0, "stocks": 0, "complete": 0, "remaining": 0, "skipped": 0}
    budget = month_budget

    for sid in targets:
        if landed.get(sid, 0) >= expected:
            continue
        if budget <= 0:
            break

        # The full bucket list every time, not a slice sized to the remaining
        # budget: `ensure_months` skips what is already stamped, and slicing
        # would ask for the most *recent* months -- which are the ones already
        # there, so a small budget would fetch nothing and look complete.
        #
        # The budget is therefore a soft cap checked between stocks. One name
        # is at most `years * 12` fetches, which bounds the overshoot.
        try:
            fetched, _ = history_service.ensure_months(db, sid, buckets)
        except Exception:
            # One delisted or malformed code must not cost the rest of the
            # universe its turn; the next run retries it.
            logger.warning("history backfill failed sid=%s", sid, exc_info=True)
            continue

        if fetched:
            stats["fetched"] += len(fetched)
            stats["stocks"] += 1
            budget -= len(fetched)

    # Re-read rather than infer. `remaining` is what an operator uses to decide
    # whether to leave the job hourly or put it back on nights, so it has to
    # mean "still incomplete now", not "skipped by this run".
    after = _landed_months(db, targets, start)
    stats["complete"] = sum(1 for sid in targets if after.get(sid, 0) >= expected)
    stats["remaining"] = len(targets) - stats["complete"]
    return stats
