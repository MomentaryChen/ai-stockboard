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

import twstock
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.stock import TPEXFetcher, TWSEFetcher

from app.config import get_settings
from app.models import DailyPrice, FetchLog
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

_FETCHERS = {"twse": TWSEFetcher, "tpex": TPEXFetcher}
MAX_FETCH_ATTEMPTS = 3


class UnknownStockError(KeyError):
    pass


def resolve_source(sid: str) -> str:
    row = twstock.codes.get(sid)
    if row is None:
        raise UnknownStockError(sid)
    return row.data_source


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
    fetcher = _FETCHERS[source]()
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


def read_prices(
    db: Session, sid: str, start: datetime.date, end: datetime.date | None = None
) -> list[DailyPrice]:
    stmt = select(DailyPrice).where(DailyPrice.sid == sid, DailyPrice.date >= start)
    if end:
        stmt = stmt.where(DailyPrice.date <= end)
    return list(db.execute(stmt.order_by(DailyPrice.date)).scalars())


def get_history(
    db: Session, sid: str, months: int, force: bool = False
) -> tuple[list[DailyPrice], list[str], list[str]]:
    buckets = month_range(months)
    fetched, cached = ensure_months(db, sid, buckets, force=force)
    start = datetime.date(buckets[0][0], buckets[0][1], 1)
    return read_prices(db, sid, start), fetched, cached
