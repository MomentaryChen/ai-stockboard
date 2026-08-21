"""Listed-instrument lookup.

Reads the `stock_code` table, which `app/services/code_sync.py` keeps in step
with the exchanges' ISIN registry -- not twstock's bundled CSVs, which age in
place and make newly listed codes look like they do not exist.

The whole listing is held in a process-wide snapshot rather than queried per
request, because the search box scans it on every keystroke: ~44k rows is a few
milliseconds in memory, against a LIKE round trip to PostgreSQL. The snapshot is
dropped whenever a sync run finishes.

If PostgreSQL is unreachable, or the table has not been seeded yet, the snapshot
falls back to twstock's bundled listing so the API degrades instead of failing.
That fallback is deliberately short-lived: the first request after the database
comes back rebuilds from the real table.
"""

import datetime
import logging
import threading
import time
from dataclasses import dataclass

import twstock
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import SEED_SYNCED_AT, StockCode
from app.schemas import StockInfo
from app.services import market_index

logger = logging.getLogger(__name__)

# Ranking only -- nothing here is filtered out. A query like 台積 should surface
# 台積電 ahead of 台積電元大53購03, and 創新板 / 特別股 / TDR ahead of a warrant
# too, which the original tuple left out.
_PREFERRED_TYPES = (
    "股票",
    "創新板",
    "ETF",
    "ETN",
    "特別股",
    "臺灣存託憑證(TDR)",
    "受益證券",
)

# A fallback snapshot is retried this often, so a database that was down at the
# first request gets picked up shortly after it returns.
_FALLBACK_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class _Row:
    """One listed instrument, in the smallest shape that answers every query.

    Deliberately not a `StockInfo`: this is held for every code at once, and a
    pydantic model costs several times what a slotted dataclass does. The
    response model is built only for the handful of rows actually returned.
    """

    code: str
    name: str
    type: str
    market: str
    group: str
    isin: str
    start: str
    data_source: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class _Snapshot:
    by_code: dict[str, _Row]
    rows: tuple[_Row, ...]
    # False when this came from twstock's bundled CSVs rather than the table.
    from_db: bool
    loaded_at: float
    synced_at: datetime.datetime | None


_lock = threading.Lock()
_snapshot: _Snapshot | None = None


def _is_warrant(type_: str) -> bool:
    """上市認購(售)權證 / 上櫃認購(售)權證.

    44k of the 46k listed codes, and never what someone typing a company name is
    looking for, so search hides them unless explicitly asked.
    """
    return "權證" in type_


def _build(
    rows: list[_Row], *, from_db: bool, synced_at: datetime.datetime | None
) -> _Snapshot:
    return _Snapshot(
        by_code={row.code: row for row in rows},
        rows=tuple(rows),
        from_db=from_db,
        loaded_at=time.monotonic(),
        synced_at=synced_at,
    )


def _bundled() -> _Snapshot:
    rows = [
        _Row(
            code=row.code,
            name=row.name,
            type=row.type,
            market=row.market,
            group=row.group or "",
            isin=row.ISIN or "",
            start=row.start or "",
            data_source=row.data_source,
            is_active=True,
        )
        for row in twstock.codes.values()
    ]
    return _build(rows, from_db=False, synced_at=None)


def _reported_sync_time(value: datetime.datetime | None) -> datetime.datetime | None:
    """Seed rows carry a sentinel stamp, which is not a real sync time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return None if value <= SEED_SYNCED_AT else value


def _from_database() -> _Snapshot | None:
    """None when the table cannot be read or has not been seeded yet."""
    # Column-wise rather than whole ORM objects: this runs on the first request
    # and hydrating 44k mapped instances would show up in the response time.
    stmt = select(
        StockCode.code,
        StockCode.name,
        StockCode.type,
        StockCode.market,
        StockCode.group,
        StockCode.isin,
        StockCode.start,
        StockCode.data_source,
        StockCode.is_active,
    )
    try:
        with SessionLocal() as db:
            rows = [_Row(*record) for record in db.execute(stmt)]
            if not rows:
                logger.warning(
                    "stock_code is empty -- serving twstock's bundled listing "
                    "until the first sync"
                )
                return None
            synced_at = db.execute(select(func.max(StockCode.synced_at))).scalar()
    except Exception:
        logger.exception("Could not read stock_code -- serving twstock's bundled listing")
        return None

    return _build(rows, from_db=True, synced_at=_reported_sync_time(synced_at))


def _current() -> _Snapshot:
    global _snapshot

    # Loading under the lock is intentional: it costs a little concurrency on
    # the very first request and saves every worker thread from issuing the
    # same 44k-row query at once.
    with _lock:
        snapshot = _snapshot
        expired = (
            snapshot is not None
            and not snapshot.from_db
            and time.monotonic() - snapshot.loaded_at > _FALLBACK_TTL_SECONDS
        )
        if snapshot is None or expired:
            snapshot = _from_database() or _bundled()
            _snapshot = snapshot
        return snapshot


def invalidate() -> None:
    """Drop the cached listing. Called by `code_sync` once it has written."""
    global _snapshot
    with _lock:
        _snapshot = None


def _to_info(row: _Row) -> StockInfo:
    return StockInfo(
        code=row.code,
        name=row.name,
        type=row.type,
        market=row.market,
        group=row.group,
        isin=row.isin,
        start=row.start,
        data_source=row.data_source,
        is_active=row.is_active,
    )


def _index_to_info(meta: market_index.IndexMeta) -> StockInfo:
    """Present an index with the same shape as a listed company.

    Everything downstream -- /api/stocks/{sid}, history, analysis -- looks the
    stock up through here, so this is all it takes for t00 / o00 to flow through
    the existing endpoints. ISIN is blank because an index has none, and an
    index is never in the exchanges' registry, so it is merged in at lookup time
    rather than stored in `stock_code`.
    """
    return StockInfo(
        code=meta.code,
        name=meta.name,
        type=meta.type,
        market=meta.market,
        group=meta.group,
        isin="",
        start=meta.start,
        data_source=meta.data_source,
    )


def get_stock(sid: str) -> StockInfo | None:
    """Resolve a sid, delisted ones included.

    Retired codes still answer here on purpose: `daily_price` holds their
    history and watchlists may still point at them. It is `search` that stops
    offering them.
    """
    meta = market_index.get(sid)
    if meta is not None:
        return _index_to_info(meta)

    row = _current().by_code.get(sid)
    return _to_info(row) if row is not None else None


def search(
    query: str, limit: int = 30, include_warrants: bool = False
) -> tuple[int, list[StockInfo]]:
    """Match against code or name. Exact code match always ranks first."""
    q = query.strip()
    if not q:
        return 0, []

    # Indices are few and always the most relevant hit, so they head the list.
    index_hits = [
        _index_to_info(meta)
        for meta in market_index.INDICES.values()
        if market_index.matches(meta, q)
    ]

    exact: list[_Row] = []
    code_prefix: list[_Row] = []
    name_hit: list[_Row] = []

    for row in _current().rows:
        if row.code == q:
            # Returned even for a delisted instrument: someone typing the whole
            # code already knows what they are asking for.
            exact.append(row)
            continue
        if not row.is_active:
            continue
        if not include_warrants and _is_warrant(row.type):
            continue
        if row.code.startswith(q):
            code_prefix.append(row)
        elif q in row.name:
            name_hit.append(row)

    def sort_key(row: _Row):
        try:
            type_rank = _PREFERRED_TYPES.index(row.type)
        except ValueError:
            type_rank = len(_PREFERRED_TYPES)
        return (type_rank, 0 if row.name == q else 1, len(row.name), row.code)

    ranked = exact + sorted(code_prefix, key=sort_key) + sorted(name_hit, key=sort_key)
    results = index_hits + [_to_info(r) for r in ranked[: max(limit - len(index_hits), 0)]]
    return len(index_hits) + len(ranked), results[:limit]


def code_count() -> int:
    """Instruments currently on offer -- what /api/health reports."""
    snapshot = _current()
    return sum(1 for row in snapshot.rows if row.is_active) + len(market_index.INDICES)


def last_synced_at() -> datetime.datetime | None:
    """When `stock_code` was last reconciled, or None if it never has been."""
    return _current().synced_at
