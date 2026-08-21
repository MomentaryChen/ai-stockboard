"""Historical daily prices, served database-first.

Flow for GET /api/stocks/{sid}/history:

  1. Work out which (year, month) buckets the requested range covers.
  2. Ask `fetch_log` which of those we already hold. Past months are immutable
     so they are never re-fetched; the current month (and empty results, which
     may be transient upstream failures) expire after CURRENT_MONTH_TTL_SECONDS.
  3. Fetch only the missing buckets from the exchange (via twstock's TWSE /
     TPEX fetchers), one month per request, each passing through the shared
     rate limiter.
  4. Upsert rows into `daily_price`, then always read the response back out of
     PostgreSQL so cached and freshly-fetched months are returned identically.
"""

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.stock import TPEXFetcher, TWSEFetcher

from app.config import get_settings
from app.models import DailyPrice, FetchLog
from app.services import codes as codes_service
from app.services import market_index
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

_FETCHERS = {"twse": TWSEFetcher, "tpex": TPEXFetcher}
MAX_FETCH_ATTEMPTS = 3


class UnknownStockError(KeyError):
    pass


def resolve_source(sid: str) -> str:
    """Which exchange fetcher this sid reports through.

    Goes through the codes service rather than twstock's bundled table so a
    freshly listed company resolves here too -- and so indices, which the
    service already merges in, need no special case.
    """
    info = codes_service.get_stock(sid)
    if info is None:
        raise UnknownStockError(sid)
    return info.data_source


def _fetcher_for(sid: str, source: str):
    """An index reports through its own exchange endpoints, not STOCK_DAY."""
    if market_index.is_index(sid):
        return market_index.IndexFetcher()
    return _FETCHERS[source]()


def month_range(months: int, today: datetime.date | None = None) -> list[tuple[int, int]]:
    """The last `months` buckets, oldest first, including the current month."""
    today = today or datetime.date.today()
    buckets = []
    year, month = today.year, today.month
    for _ in range(months):
        buckets.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(buckets))


def _is_stale(log: FetchLog, year: int, month: int, now: datetime.datetime) -> bool:
    today = now.date()
    is_current_month = (year, month) == (today.year, today.month)
    # An empty bucket may just be an upstream hiccup -- let it expire and retry.
    if not is_current_month and log.row_count > 0:
        return False

    fetched_at = log.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    age = (now - fetched_at).total_seconds()
    return age > settings.current_month_ttl_seconds


def _fetch_month(source: str, sid: str, year: int, month: int) -> list:
    """One upstream request per attempt, each gated by the rate limiter."""
    fetcher = _fetcher_for(sid, source)
    for attempt in range(MAX_FETCH_ATTEMPTS):
        twse_throttle.acquire()
        try:
            # retry=1: we drive retries ourselves so every attempt is throttled.
            result = fetcher.fetch(year, month, sid, retry=1)
        except Exception:
            logger.warning(
                "fetch failed sid=%s %04d-%02d attempt=%d", sid, year, month, attempt + 1,
                exc_info=True,
            )
            continue
        rows = result.get("data") or []
        if rows:
            return rows
    return []


def _upsert(db: Session, sid: str, rows: list) -> int:
    if not rows:
        return 0

    payload = [
        {
            "sid": sid,
            "date": r.date.date() if isinstance(r.date, datetime.datetime) else r.date,
            "capacity": r.capacity,
            "turnover": r.turnover,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "change": r.change,
            "transaction": r.transaction,
            "note": (r.note or "")[:64],
        }
        for r in rows
    ]

    stmt = pg_insert(DailyPrice).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyPrice.sid, DailyPrice.date],
        set_={
            c: stmt.excluded[c]
            for c in (
                "capacity", "turnover", "open", "high", "low",
                "close", "change", "transaction", "note",
            )
        },
    )
    db.execute(stmt)
    return len(payload)


def _log_fetch(db: Session, sid: str, year: int, month: int, source: str, count: int) -> None:
    stmt = pg_insert(FetchLog).values(
        sid=sid, year=year, month=month, source=source, row_count=count,
        fetched_at=datetime.datetime.now(datetime.timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FetchLog.sid, FetchLog.year, FetchLog.month],
        set_={
            "row_count": stmt.excluded.row_count,
            "source": stmt.excluded.source,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    db.execute(stmt)


def ensure_months(
    db: Session, sid: str, buckets: list[tuple[int, int]], force: bool = False
) -> tuple[list[str], list[str]]:
    """Make sure every bucket is in the DB. Returns (fetched, cached) month labels."""
    source = resolve_source(sid)
    now = datetime.datetime.now(datetime.timezone.utc)

    logs = {
        (log.year, log.month): log
        for log in db.execute(
            select(FetchLog).where(FetchLog.sid == sid)
        ).scalars()
    }

    fetched: list[str] = []
    cached: list[str] = []

    for year, month in buckets:
        log = logs.get((year, month))
        if not force and log is not None and not _is_stale(log, year, month, now):
            cached.append(f"{year}-{month:02d}")
            continue

        rows = _fetch_month(source, sid, year, month)
        _upsert(db, sid, rows)
        _log_fetch(db, sid, year, month, source, len(rows))
        db.commit()
        fetched.append(f"{year}-{month:02d}")

    return fetched, cached


def months_are_cached(db: Session, sid: str, buckets: list[tuple[int, int]]) -> bool:
    """Would `ensure_months` for these buckets touch the exchange?

    The same question `ensure_months` asks per bucket, asked without answering
    it -- no fetch, no upsert, no commit. It exists so a caller can find out
    whether `read_prices` already equals what `get_history` would return.

    That equivalence is what lets the AI route probe its verdict cache before
    loading history: a probe over bars the exchange has since moved past would
    answer for the wrong trading day, and this is what rules that out. Sharing
    `_is_stale` rather than restating "recent enough" is the point -- a second
    freshness rule would drift from the first one and the drift would show up
    as a stale verdict, which is the bug being avoided.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    logs = {
        (log.year, log.month): log
        for log in db.execute(select(FetchLog).where(FetchLog.sid == sid)).scalars()
    }
    return all(
        (log := logs.get(bucket)) is not None
        and not _is_stale(log, bucket[0], bucket[1], now)
        for bucket in buckets
    )


def cached_sids(
    db: Session, sids: list[str], buckets: list[tuple[int, int]]
) -> set[str]:
    """Which of `sids` hold every bucket fresh -- `months_are_cached`, batched.

    One query for the whole basket rather than one per sid: the AI board asks
    this about twenty stocks on every load, and the per-sid form would turn a
    batch route into twenty round trips to Postgres wearing one URL.
    """
    if not sids:
        return set()

    now = datetime.datetime.now(datetime.timezone.utc)
    wanted = set(buckets)
    fresh: dict[str, set[tuple[int, int]]] = {sid: set() for sid in sids}

    for log in db.execute(select(FetchLog).where(FetchLog.sid.in_(sids))).scalars():
        bucket = (log.year, log.month)
        if bucket in wanted and not _is_stale(log, log.year, log.month, now):
            fresh[log.sid].add(bucket)

    return {sid for sid, held in fresh.items() if held >= wanted}


def read_prices(
    db: Session, sid: str, start: datetime.date, end: datetime.date | None = None
) -> list[DailyPrice]:
    stmt = select(DailyPrice).where(DailyPrice.sid == sid, DailyPrice.date >= start)
    if end:
        stmt = stmt.where(DailyPrice.date <= end)
    return list(db.execute(stmt.order_by(DailyPrice.date)).scalars())


def read_prices_many(
    db: Session, sids: list[str], start: datetime.date, end: datetime.date | None = None
) -> dict[str, list[DailyPrice]]:
    """Cached daily bars for many sids, one query, no upstream fetch.

    The watchlist BFP board uses this so scoring 20 stocks cannot queue
    behind (or crowd out) the realtime poll on the shared TWSE limiter.

    `end` matters to callers that ask about a *specific* past session rather
    than about the trailing window: the open board reads a date the user picked,
    and rows after it would make "the bar before this one" the wrong bar.
    """
    grouped: dict[str, list[DailyPrice]] = {sid: [] for sid in sids}
    if not sids:
        return grouped
    stmt = select(DailyPrice).where(DailyPrice.sid.in_(sids), DailyPrice.date >= start)
    if end:
        stmt = stmt.where(DailyPrice.date <= end)
    stmt = stmt.order_by(DailyPrice.sid, DailyPrice.date)
    for row in db.execute(stmt).scalars():
        grouped[row.sid].append(row)
    return grouped


def get_history(
    db: Session, sid: str, months: int, force: bool = False
) -> tuple[list[DailyPrice], list[str], list[str]]:
    buckets = month_range(months)
    fetched, cached = ensure_months(db, sid, buckets, force=force)
    start = datetime.date(buckets[0][0], buckets[0][1], 1)
    return read_prices(db, sid, start), fetched, cached
