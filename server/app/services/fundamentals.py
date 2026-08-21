"""Annual EPS and ROE, served database-first.

Today this module only reads and writes `fundamentals_annual`; nothing fills it
automatically. That is deliberate rather than unfinished. Choosing a provider
is a licensing and reliability decision (FinMind's free tier, MOPS scraping,
and a hand-maintained CSV all have different answers), and the 存股 checklist
above it was built to report `unknown` for the dimensions it cannot compute --
so shipping the store and the seam ahead of the ingest costs nothing and lets
every caller be written against the shape the ingest will fill.

`upsert()` is the seam. A provider client is a function that produces `Row`s;
it does not need to know about the table, the fetch log or the conflict
handling, all of which are here. `record_fetch()` mirrors the dividend
ingest's own fetch stamp, so a future job can say what it pulled and when in
the vocabulary the other ingests already use.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.proxy import get_proxies, get_session

from app.config import get_settings
from app.models import FundamentalsAnnual, FundamentalsFetchLog
from app.services import finmind
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

#: Everything the checklist reads. Wider than what any one provider returns, so
#: a client fills what it has and leaves the rest None -- the store keeps
#: "unknown" and "zero" distinct all the way to the card.
_UPDATABLE = ("eps", "roe", "net_income", "equity", "source")


@dataclass(frozen=True, slots=True)
class Row:
    """One company-year, as a provider client produces it."""

    sid: str
    year: int
    eps: float | None = None
    roe: float | None = None
    net_income: float | None = None
    equity: float | None = None
    source: str = "manual"


def read(db: Session, sid: str, years: int) -> list[FundamentalsAnnual]:
    """Stored company-years for one sid, oldest first.

    Never fetches. Unlike history and dividends there is no lazy upstream to
    fall back to, so an empty list means the ingest has not covered this name --
    which the checklist reports rather than treats as a failure.
    """
    today = datetime.date.today()
    return list(
        db.execute(
            select(FundamentalsAnnual)
            .where(
                FundamentalsAnnual.sid == sid,
                FundamentalsAnnual.year > today.year - years - 1,
            )
            .order_by(FundamentalsAnnual.year)
        ).scalars()
    )


def upsert(db: Session, rows: list[Row]) -> int:
    """Write company-years, replacing what a previous pull left.

    Replacing rather than merging: a provider correcting a restated figure has
    to be able to overwrite it, and a partial row from a thinner source would
    otherwise be indistinguishable from a correction that blanked a field.
    """
    if not rows:
        return 0

    payload = [
        {
            "sid": row.sid,
            "year": row.year,
            "eps": row.eps,
            "roe": row.roe,
            "net_income": row.net_income,
            "equity": row.equity,
            "source": row.source[:16],
        }
        for row in rows
    ]
    stmt = pg_insert(FundamentalsAnnual).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalsAnnual.sid, FundamentalsAnnual.year],
        set_={c: stmt.excluded[c] for c in _UPDATABLE},
    )
    db.execute(stmt)
    return len(payload)


def upsert_partial(db: Session, rows: list[Row]) -> int:
    """Write only the columns a row actually carries, leaving the rest alone.

    The counterpart to `upsert`, and the two exist because two ingests fill
    different halves of the same row. The exchange report has EPS and net
    income but no equity, so it cannot produce an ROE; the FinMind backfill
    produces all four. A plain upsert from the nightly exchange pull would
    write NULL over the ROE and equity the backfill spent an hour collecting,
    and the Efficient dimension would silently go `unknown` again every night.

    `COALESCE(excluded, existing)` is what makes the two additive: an incoming
    NULL defers to what is already stored, an incoming value wins.

    `source` is not coalesced -- it names the most recent writer. Per-column
    provenance would need a column per field, and the audit that matters
    (`net_income` and `equity` beside `roe`) is already in the row.
    """
    if not rows:
        return 0

    payload = [
        {
            "sid": row.sid,
            "year": row.year,
            "eps": row.eps,
            "roe": row.roe,
            "net_income": row.net_income,
            "equity": row.equity,
            "source": row.source[:16],
        }
        for row in rows
    ]
    stmt = pg_insert(FundamentalsAnnual).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalsAnnual.sid, FundamentalsAnnual.year],
        set_={
            "source": stmt.excluded.source,
            **{
                c: func.coalesce(stmt.excluded[c], getattr(FundamentalsAnnual, c))
                for c in ("eps", "roe", "net_income", "equity")
            },
        },
    )
    db.execute(stmt)
    return len(payload)


def record_fetch(db: Session, source: str, bucket: str, count: int) -> None:
    """Stamp one provider pull, the same way the dividend ingest does."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = pg_insert(FundamentalsFetchLog).values(
        source=source, bucket=bucket, row_count=count, fetched_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalsFetchLog.source, FundamentalsFetchLog.bucket],
        set_={
            "row_count": stmt.excluded.row_count,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    db.execute(stmt)


# --- exchange ingest: the current annual figure, free and keyless ------------

TWSE_EPS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap14_L"
TPEX_EPS_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O"

#: `t187ap14` is published **cumulative year-to-date**, so 季別 4 -- and only
#: 季別 4 -- is the annual figure. Verified against FinMind's per-quarter
#: series: 台泥 115Q2 reads 0.38, which is Q1+Q2 (0.10+0.29), not Q2's 0.29.
#: Storing a Q2 row as the year's EPS would understate every company's
#: earnings and fail the Earn rule across the whole board.
ANNUAL_QUARTER = "4"

#: Field names differ between the two exchanges for the same report.
_TWSE_EPS_FIELDS = {
    "sid": "公司代號",
    "year": "年度",
    "quarter": "季別",
    "eps": "基本每股盈餘(元)",
    "net_income": "稅後淨利",
}
_TPEX_EPS_FIELDS = {
    "sid": "SecuritiesCompanyCode",
    "year": "Year",
    "quarter": "季別",
    "eps": "基本每股盈餘",
    "net_income": "稅後淨利",
}

#: `稅後淨利` in this report is in thousands of NTD; FinMind reports the same
#: quantity in units. Normalised on the way in so `net_income` means one thing
#: regardless of which ingest wrote the row -- otherwise an ROE audit would be
#: out by a factor of a thousand depending on provenance.
_TWD_THOUSANDS = 1000

FINMIND_SOURCE = "finmind"


def _num(text: object) -> float | None:
    if text is None:
        return None
    raw = str(text).strip().replace(",", "")
    if not raw or raw in {"-", "--", "N/A"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _roc_year(text: object) -> int | None:
    value = _num(text)
    if value is None:
        return None
    year = int(value)
    # The report stamps ROC years (115). A Gregorian one is accepted too, in
    # case the field is ever normalised upstream.
    return year + 1911 if year < 1000 else year


def parse_exchange_eps(payload: object, fields: dict[str, str], source: str) -> list[Row]:
    """Annual rows from one exchange's EPS report. Non-Q4 rows are dropped."""
    if not isinstance(payload, list):
        return []

    rows: list[Row] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get(fields["quarter"]) or "").strip() != ANNUAL_QUARTER:
            continue
        sid = str(item.get(fields["sid"]) or "").strip()
        year = _roc_year(item.get(fields["year"]))
        if not sid or year is None:
            continue
        net_income = _num(item.get(fields["net_income"]))
        rows.append(
            Row(
                sid=sid,
                year=year,
                eps=_num(item.get(fields["eps"])),
                # The report carries no equity, so no ROE can be derived from
                # it. Left None rather than guessed; the FinMind backfill is
                # what fills that column.
                roe=None,
                net_income=net_income * _TWD_THOUSANDS if net_income is not None else None,
                equity=None,
                source=source,
            )
        )
    return rows


def _fetch_json(url: str) -> object:
    session = get_session()
    for attempt in range(3):
        twse_throttle.acquire()
        try:
            return session.get(url, proxies=get_proxies(), timeout=30).json()
        except Exception:
            logger.warning(
                "fundamentals fetch failed url=%s attempt=%d", url, attempt + 1,
                exc_info=True,
            )
    return None


def _stale(log: FundamentalsFetchLog, now: datetime.datetime) -> bool:
    """The current year's bucket expires; a completed year's never does."""
    fetched_at = log.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    return (now - fetched_at).total_seconds() > settings.current_month_ttl_seconds


def refresh_exchange(db: Session, *, force: bool = False) -> dict[str, int]:
    """Pull the current annual EPS board from both exchanges. Two requests.

    Only Q4 rows are written, so for most of the year this correctly finds
    nothing to store: the report exists, it just is not an annual figure yet.

    Uses `upsert_partial` rather than `upsert`, which is the whole reason that
    function exists. A plain upsert writes every column in `_UPDATABLE`, and
    these rows carry no ROE or equity -- so the nightly exchange pull would
    blank exactly the columns the FinMind backfill spent an hour filling.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    bucket = str(now.year)

    stats = {"fetched": 0, "cached": 0, "rows": 0}
    for source, url, fields in (
        ("twse_eps", TWSE_EPS_URL, _TWSE_EPS_FIELDS),
        ("tpex_eps", TPEX_EPS_URL, _TPEX_EPS_FIELDS),
    ):
        log = db.get(FundamentalsFetchLog, (source, bucket))
        if not force and log is not None and not _stale(log, now):
            stats["cached"] += 1
            continue

        rows = parse_exchange_eps(_fetch_json(url), fields, source)
        stats["rows"] += upsert_partial(db, rows)
        record_fetch(db, source, bucket, len(rows))
        stats["fetched"] += 1
        db.commit()

    return stats


# --- FinMind backfill: the decade the exchanges do not publish ---------------


def pending_backfill(db: Session, sids: list[str], limit: int) -> list[str]:
    """Companies the history backfill has not covered yet.

    Resumability is the whole design. A full sweep is two requests per company
    across roughly eighteen hundred of them, which no single run should
    attempt against an hourly quota -- so each run takes a slice, stamps what
    it did, and the next run continues. `fundamentals_fetch_log` keyed
    (source=finmind, bucket=sid) is exactly the per-company shape the store
    was documented to support.
    """
    done = {
        log.bucket
        for log in db.execute(
            select(FundamentalsFetchLog).where(
                FundamentalsFetchLog.source == FINMIND_SOURCE
            )
        ).scalars()
    }
    return [sid for sid in sids if sid not in done][:limit]


def _is_quota_error(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("402", "quota", "limit", "too many"))


def backfill_history(
    db: Session, sids: list[str], *, years: int, limit: int
) -> dict[str, int]:
    """Fill annual history for up to `limit` companies that have none yet.

    One company's failure is stamped and skipped rather than raised: a
    delisted code, or one FinMind has no statements for, must not cost the
    other nine hundred their turn. A *quota* refusal is different and ends the
    run -- continuing would spend the rest of the allowance on certain
    failures, and the next scheduled run resumes from here anyway.
    """
    targets = pending_backfill(db, sids, limit)
    stats = {"stocks": 0, "rows": 0, "empty": 0, "failed": 0}

    for sid in targets:
        try:
            annual = finmind.fetch_annual(sid, years)
        except finmind.FinMindFailed as exc:
            message = str(exc)
            logger.warning("finmind backfill failed sid=%s: %s", sid, message)
            stats["failed"] += 1
            if _is_quota_error(message):
                logger.warning("finmind quota reached; ending run early")
                break
            continue

        rows = [
            Row(
                sid=sid,
                year=item["year"],
                eps=item["eps"],
                roe=item["roe"],
                net_income=item["net_income"],
                equity=item["equity"],
                source=FINMIND_SOURCE,
            )
            for item in annual
        ]
        stats["rows"] += upsert(db, rows)
        stats["stocks"] += 1
        if not rows:
            stats["empty"] += 1
        # Stamped even when empty: a company FinMind has nothing for must not
        # be retried on every run for ever.
        record_fetch(db, FINMIND_SOURCE, sid, len(rows))
        db.commit()

    return stats
