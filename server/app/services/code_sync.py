"""Keeping `stock_code` in step with the exchanges' ISIN registry.

twstock ships the listed-instrument table as two CSVs baked into its package,
and refreshes them through `twstock.codes.__update_codes()`, which rewrites
those files inside the installed package directory. That is unusable here: in
the container the tree is owned by root while the process runs as `appuser`,
and anything it did manage to write would vanish on the next restart. The
practical effect is a snapshot that silently ages -- the copy vendored in this
repo stops at 2026/03/31 -- so every instrument listed after it answers 404 as
though it did not exist.

So the listing lives in PostgreSQL and this module owns it:

  * seed    -- an empty table is filled from twstock's bundled snapshot before
               anything touches the network, so a boot with no internet still
               has a usable listing.
  * refresh -- isin.twse.com.tw is scraped for 上市 (strMode=2) and 上櫃
               (strMode=4) and upserted, on startup and every interval after.
  * retire  -- codes the registry no longer carries are flagged inactive rather
               than deleted: `daily_price` and `watchlist_item` point at them,
               and a delisted company's history is still worth reading.
  * prune   -- except for warrants. They expire in their tens of thousands (the
               first real run retired 29,062 of them against 7 real
               instruments) and nothing charts an expired warrant, so they are
               deleted once the retention window has passed. That is what keeps
               both the table and the in-memory listing bounded.

Every attempt -- including the ones that skip because the listing is still
fresh, and the ones that fail -- lands in `stock_code_sync_run`. That table is
the only way to distinguish a job with nothing to do from one that has been
failing quietly for a fortnight, since both leave `stock_code` untouched.

Deliberately not coordinated across replicas. The freshness check means a
restart costs nothing, and the worst a second replica can do is fetch the same
two pages once a day and write the same rows -- the upsert is idempotent, and
a lock held across a 25-second scrape would cost more than it saves.
"""

import datetime
import logging
import threading
import time
from dataclasses import dataclass

import twstock
from lxml import etree
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.proxy import get_proxies, get_session

from app.config import get_settings
from app.db import SessionLocal
from app.models import MAX_SYNC_RUNS_KEPT, SEED_SYNCED_AT, StockCode, StockCodeSyncRun
from app.services import codes as codes_service
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

ISIN_URLS = {
    "twse": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",  # 上市
    "tpex": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",  # 上櫃
}

# The 上市 page is an 8 MB HTML table and regularly takes ~25 s to arrive.
TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 3

# One INSERT per chunk. 44k rows in a single statement is a multi-megabyte
# query that psycopg has to build in memory first.
CHUNK_SIZE = 2000

# A failed run retries on this shorter cadence instead of waiting out the full
# interval -- otherwise one unreachable registry costs a whole day.
RETRY_SECONDS = 600

# How long a retired warrant is kept before being deleted outright. The delay is
# the safety margin: a run that wrongly retired half the market has this long to
# be corrected by a good one, which revives the rows instead of finding them
# gone. Anything that is not a warrant is kept indefinitely.
RETIRED_WARRANT_RETENTION_DAYS = 30

# The registry separates code and name with an ideographic space.
_SEPARATOR = "　"

# Columns an upsert refreshes. `code` is the conflict target, so it is not here.
_UPDATABLE = (
    "name",
    "type",
    "market",
    "group",
    "isin",
    "start",
    "data_source",
    "is_active",
    "delisted_at",
    "synced_at",
)


@dataclass(frozen=True)
class SyncReport:
    status: str  # synced | skipped | failed
    synced_at: datetime.datetime | None
    active: int
    inserted: int
    updated: int
    delisted: int
    pruned: int = 0
    message: str | None = None


# Where a run came from, recorded so the admin view can tell the nightly job
# apart from someone pressing the button.
TRIGGER_STARTUP = "startup"
TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


# --------------------------------------------------------------------------
# Scraping the registry
# --------------------------------------------------------------------------


def _parse(html: str) -> list[dict]:
    """Rows out of one ISIN registry page.

    Same shape twstock's own scraper produces, but tolerant of what it would
    crash on. The page interleaves section headers with data rows, and a single
    malformed entry must not cost us the other 44,000 -- twstock's version
    unpacks `code, name = cell.split()` and dies on the first row without a
    separator.
    """
    root = etree.HTML(html)
    if root is None:
        return []

    rows: list[dict] = []
    section = ""
    for tr in root.xpath("//tr"):
        # `iter()` yields the row element itself before its cells, which is why
        # the offsets below start at 1.
        cells = [(node.text or "").strip() for node in tr.iter()]

        # A four-entry row is the banner naming the section every row beneath
        # it belongs to: 股票, ETF, 上市認購(售)權證, ...
        if len(cells) == 4:
            section = cells[2]
            continue
        # Anything without a code/name cell is the column header or a spacer.
        if len(cells) < 7 or _SEPARATOR not in cells[1]:
            continue

        code, _, name = cells[1].partition(_SEPARATOR)
        code = code.strip()
        if not code:
            continue
        rows.append(
            {
                "code": code[:16],
                "name": name.strip()[:64],
                "type": section[:32],
                "isin": cells[2][:24],
                "start": cells[3][:16],
                "market": cells[4][:16],
                "group": cells[5][:64],
            }
        )
    return rows


def _fetch(source: str) -> list[dict]:
    """One market's listing, or an empty list if the registry is unreachable."""
    url = ISIN_URLS[source]
    for attempt in range(MAX_ATTEMPTS):
        # Shares the limiter with price fetches: a different host, but the same
        # exchange behind it.
        twse_throttle.acquire()
        try:
            response = get_session().get(
                url, proxies=get_proxies(), timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
            # The page declares charset=MS950 in its Content-Type, so requests
            # decodes it correctly without help.
            rows = _parse(response.text)
        except Exception:
            logger.warning(
                "ISIN registry fetch failed source=%s attempt=%d",
                source,
                attempt + 1,
                exc_info=True,
            )
            continue

        if rows:
            for row in rows:
                row["data_source"] = source
            logger.info("ISIN registry source=%s rows=%d", source, len(rows))
            return rows

        logger.warning(
            "ISIN registry parsed to nothing source=%s attempt=%d -- page layout may have changed",
            source,
            attempt + 1,
        )
    return []


def _bundled_rows() -> list[dict]:
    """twstock's CSV snapshot, used only to fill an empty table."""
    return [
        {
            "code": row.code[:16],
            "name": row.name[:64],
            "type": row.type[:32],
            "market": row.market[:16],
            "group": (row.group or "")[:64],
            "isin": (row.ISIN or "")[:24],
            "start": (row.start or "")[:16],
            "data_source": row.data_source,
        }
        for row in twstock.codes.values()
    ]


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _write(db: Session, rows: list[dict], stamp: datetime.datetime) -> int:
    """Upsert every row, stamping it as seen at `stamp`. Returns rows written.

    Deduplicates first: PostgreSQL refuses an ON CONFLICT statement that would
    touch the same row twice, so one code appearing in both market listings
    would otherwise fail the whole chunk.
    """
    by_code = {row["code"]: row for row in rows if row["code"]}
    payload = [
        {**row, "is_active": True, "delisted_at": None, "synced_at": stamp}
        for row in by_code.values()
    ]

    for start in range(0, len(payload), CHUNK_SIZE):
        chunk = payload[start : start + CHUNK_SIZE]
        stmt = pg_insert(StockCode).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[StockCode.code],
            set_={column: stmt.excluded[column] for column in _UPDATABLE},
        )
        db.execute(stmt)

    return len(payload)


def _retire(db: Session, run_at: datetime.datetime) -> int:
    """Flag everything this run did not see as delisted.

    Keyed on `synced_at` rather than a NOT IN over 44k codes: every row the run
    saw was just stamped with `run_at`, so anything older was dropped from the
    registry.
    """
    result = db.execute(
        update(StockCode)
        .where(StockCode.is_active.is_(True), StockCode.synced_at < run_at)
        .values(is_active=False, delisted_at=run_at)
    )
    return result.rowcount or 0


def _record_run(
    db: Session,
    *,
    started_at: datetime.datetime,
    trigger: str,
    report: SyncReport,
    sources: list[str],
) -> None:
    """Append this attempt to the audit trail and trim the trail.

    Committed by the caller alongside whatever the run wrote, so the log and the
    listing can never disagree about what happened.
    """
    db.add(
        StockCodeSyncRun(
            started_at=started_at,
            finished_at=_now(),
            status=report.status,
            trigger=trigger,
            sources=",".join(sources)[:32],
            active=report.active,
            inserted=report.inserted,
            updated=report.updated,
            delisted=report.delisted,
            pruned=report.pruned,
            message=report.message[:255] if report.message else None,
        )
    )
    db.flush()  # the row needs an id before it can be counted out of the window

    # Keep the newest MAX_SYNC_RUNS_KEPT. Comparing on the id of the oldest
    # survivor is one statement, and ids are monotonic here.
    cutoff = db.execute(
        select(StockCodeSyncRun.id)
        .order_by(StockCodeSyncRun.id.desc())
        .offset(MAX_SYNC_RUNS_KEPT)
        .limit(1)
    ).scalar()
    if cutoff is not None:
        db.execute(delete(StockCodeSyncRun).where(StockCodeSyncRun.id <= cutoff))


def list_runs(db: Session, limit: int = 50) -> tuple[int, list[StockCodeSyncRun]]:
    """Most recent attempts first."""
    total = db.execute(select(func.count()).select_from(StockCodeSyncRun)).scalar_one()
    rows = db.execute(
        select(StockCodeSyncRun).order_by(StockCodeSyncRun.started_at.desc()).limit(limit)
    ).scalars()
    return total, list(rows)


def last_success_at(db: Session) -> datetime.datetime | None:
    """Start time of the newest run that actually wrote a listing."""
    value = db.execute(
        select(func.max(StockCodeSyncRun.started_at)).where(
            StockCodeSyncRun.status == "synced"
        )
    ).scalar()
    return _as_utc(value) if value is not None else None


def _prune_expired_warrants(db: Session, now: datetime.datetime) -> int:
    """Delete warrants retired longer ago than the retention window."""
    cutoff = now - datetime.timedelta(days=RETIRED_WARRANT_RETENTION_DAYS)
    result = db.execute(
        delete(StockCode).where(
            StockCode.is_active.is_(False),
            StockCode.type.like("%權證%"),
            StockCode.delisted_at < cutoff,
        )
    )
    return result.rowcount or 0


def _count(db: Session, active_only: bool = False) -> int:
    stmt = select(func.count()).select_from(StockCode)
    if active_only:
        stmt = stmt.where(StockCode.is_active.is_(True))
    return db.execute(stmt).scalar_one()


def last_synced_at(db: Session) -> datetime.datetime | None:
    """When the table was last reconciled, or None if it never has been."""
    value = db.execute(select(func.max(StockCode.synced_at))).scalar()
    if value is None:
        return None
    value = _as_utc(value)
    return None if value <= SEED_SYNCED_AT else value


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def run(
    db: Session, force: bool = False, trigger: str = TRIGGER_MANUAL
) -> SyncReport:
    """Seed if empty, then reconcile with the registry unless it is still fresh.

    Every exit path records a row in `stock_code_sync_run` before returning, so
    the audit trail has no holes -- a failure that wrote nothing is exactly the
    case you most need to see afterwards.
    """
    started_at = _now()

    if _count(db) == 0:
        seeded = _write(db, _bundled_rows(), SEED_SYNCED_AT)
        db.commit()
        codes_service.invalidate()
        logger.info("Seeded stock_code with %d rows from twstock's snapshot", seeded)

    last = last_synced_at(db)
    if not force and last is not None:
        age = (_now() - last).total_seconds()
        if age < settings.stock_code_sync_interval_hours * 3600:
            report = SyncReport(
                status="skipped",
                synced_at=last,
                active=_count(db, active_only=True),
                inserted=0,
                updated=0,
                delisted=0,
                message=f"Listing was reconciled {int(age // 60)} minutes ago",
            )
            _record_run(
                db, started_at=started_at, trigger=trigger, report=report, sources=[]
            )
            db.commit()
            return report

    run_at = _now()
    rows: list[dict] = []
    seen_sources: list[str] = []
    missing: list[str] = []
    for source in ISIN_URLS:
        market_rows = _fetch(source)
        if market_rows:
            rows.extend(market_rows)
            seen_sources.append(source)
        else:
            missing.append(source)
            logger.error(
                "ISIN registry unavailable for %s -- keeping the stored rows", source
            )

    if not rows:
        report = SyncReport(
            status="failed",
            synced_at=last,
            active=_count(db, active_only=True),
            inserted=0,
            updated=0,
            delisted=0,
            message="Could not reach the ISIN registry",
        )
        _record_run(
            db, started_at=started_at, trigger=trigger, report=report, sources=[]
        )
        db.commit()
        return report

    before = _count(db)
    written = _write(db, rows, run_at)
    after = _count(db)
    inserted = after - before

    # Only a run that saw *both* markets may retire anything. Half a listing
    # would otherwise delist every code belonging to the market that failed.
    delisted = _retire(db, run_at) if not missing else 0
    pruned = _prune_expired_warrants(db, run_at)

    message = None
    if missing:
        message = (
            f"Partial: {', '.join(missing)} unavailable, so nothing was retired"
        )

    report = SyncReport(
        status="synced",
        synced_at=run_at,
        active=_count(db, active_only=True),
        inserted=inserted,
        updated=written - inserted,
        delisted=delisted,
        pruned=pruned,
        message=message,
    )
    _record_run(
        db,
        started_at=started_at,
        trigger=trigger,
        report=report,
        sources=seen_sources,
    )
    db.commit()
    codes_service.invalidate()
    logger.info(
        "stock_code synced: active=%d inserted=%d updated=%d delisted=%d pruned=%d%s",
        report.active,
        report.inserted,
        report.updated,
        report.delisted,
        report.pruned,
        f" ({message})" if message else "",
    )
    return report


def _loop() -> None:
    interval = max(settings.stock_code_sync_interval_hours, 1) * 3600
    trigger = TRIGGER_STARTUP
    while True:
        delay = interval
        try:
            with SessionLocal() as db:
                report = run(db, trigger=trigger)
            if report.status == "failed":
                delay = RETRY_SECONDS
                logger.warning(
                    "stock_code sync failed (%s) -- retrying in %d s",
                    report.message,
                    delay,
                )
        except Exception:
            delay = RETRY_SECONDS
            logger.exception("stock_code sync errored -- retrying in %d s", delay)
        trigger = TRIGGER_SCHEDULE
        time.sleep(delay)


def start_scheduler() -> None:
    """Run the sync in the background, now and every interval after.

    A daemon thread rather than an async task: everything below it -- requests,
    psycopg, the rate limiter -- is blocking, and the first run scrapes for the
    better part of a minute, which must not hold up startup or the container
    healthcheck.
    """
    if not settings.stock_code_sync_enabled:
        logger.warning(
            "STOCK_CODE_SYNC_ENABLED is off -- stock_code will not learn about new listings"
        )
        return

    threading.Thread(target=_loop, name="stock-code-sync", daemon=True).start()
    logger.info(
        "stock_code sync scheduled every %d h", settings.stock_code_sync_interval_hours
    )
