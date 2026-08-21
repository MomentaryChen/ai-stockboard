"""個股本益比、殖利率、股價淨值比, served database-first.

Both exchanges publish these three figures as an all-market daily report --
TWSE's BWIBBU and TPEx's peratio analysis -- so one request per source fills
every listed name for one session. That is the same economics as the T86 /
MI_MARGN reports in `chip.py`, and this module is deliberately its sibling
rather than something new: fetch the board, upsert, stamp the bucket, and let
the second reader of that session pay nothing.

Why store the exchange's PE instead of computing one. A PE derived here would
be last year's annual EPS over today's close, which lags a turnaround by up to
a year and is undefined for anything that has not filed yet. The exchange
maintains a trailing figure from quarterly filings and publishes it daily. For
a checklist whose whole point is not overpaying, using their number and saying
so is better than deriving a worse one and not saying so.

The rows also accumulate, which is what will make "cheap against its own
five-year band" -- the half of 買得便宜 a fixed sector ceiling cannot express --
a query rather than a second ingest.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.proxy import get_proxies, get_session

from app.config import get_settings
from app.models import FundamentalsFetchLog, ValuationDay
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"

MAX_ATTEMPTS = 3
TIMEOUT = 30

#: `fundamentals_fetch_log` is keyed (source, bucket) and its bucket is
#: whatever the provider's unit of work is. Here the unit is a session, so the
#: bucket is the date and the source names the report -- which keeps this out
#: of the way of the per-company FinMind buckets in `fundamentals.py`.
TWSE_SOURCE = "twse_val"
TPEX_SOURCE = "tpex_val"


@dataclass(frozen=True, slots=True)
class _Row:
    sid: str
    date: datetime.date
    pe_ratio: float | None
    pb_ratio: float | None
    dividend_yield: float | None
    source: str


def _to_float(text: object) -> float | None:
    """A published figure, or None when the exchange left it undefined.

    Blank is the common case and it is meaningful: TWSE omits PE for a company
    with no positive trailing earnings. `0` would claim the company trades at
    zero times earnings, so an unparseable value has to stay null all the way
    into the column.
    """
    if text is None:
        return None
    raw = str(text).strip().replace(",", "")
    if not raw or raw in {"-", "--", "N/A", "n/a"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    # The exchanges use 0 as a filler for "not applicable" on PE and PBR often
    # enough that treating it as a real ratio would put loss-making companies
    # at the top of a cheapness screen.
    return value if value > 0 else None


def _roc_date(text: object) -> datetime.date | None:
    """TWSE stamps its OpenAPI rows '1150820' -- ROC year, month, day."""
    raw = str(text or "").strip()
    if not raw.isdigit() or len(raw) not in (6, 7):
        return None
    head = len(raw) - 4
    try:
        return datetime.date(
            int(raw[:head]) + 1911, int(raw[head : head + 2]), int(raw[head + 2 :])
        )
    except ValueError:
        return None


def _get_json(url: str) -> object:
    session = get_session()
    for attempt in range(MAX_ATTEMPTS):
        twse_throttle.acquire()
        try:
            response = session.get(url, proxies=get_proxies(), timeout=TIMEOUT)
            return response.json()
        except Exception:
            logger.warning(
                "valuation fetch failed url=%s attempt=%d", url, attempt + 1,
                exc_info=True,
            )
    return None


def parse_twse(payload: object, fallback: datetime.date) -> list[_Row]:
    """BWIBBU_ALL: one object per listed stock, each carrying its own date."""
    if not isinstance(payload, list):
        return []
    rows: list[_Row] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("Code") or "").strip()
        if not sid:
            continue
        rows.append(
            _Row(
                sid=sid,
                date=_roc_date(item.get("Date")) or fallback,
                pe_ratio=_to_float(item.get("PEratio")),
                pb_ratio=_to_float(item.get("PBratio")),
                dividend_yield=_to_float(item.get("DividendYield")),
                source="twse",
            )
        )
    return rows


#: TPEx field names for the three figures, in its own vocabulary.
#:
#: Matched exactly rather than by substring, which is not fussiness: TPEx also
#: publishes `DividendPerShare`, and "DividendPerShare".lower() contains "per",
#: so a loose match for the PE ratio silently stores dividend-per-share as a
#: PE. A 0.5 "PE" would then pass every cheapness threshold there is.
_TPEX_FIELDS = {
    "sid": "SecuritiesCompanyCode",
    "date": "Date",
    "pe": "PriceEarningRatio",
    "pb": "PriceBookRatio",
    "yield": "YieldRatio",
}


def parse_tpex(payload: object, fallback: datetime.date) -> list[_Row]:
    """TPEx publishes the same three figures under its own field names.

    A renamed field costs that column a null rather than dropping the board,
    because `_to_float(None)` is None -- but a *renamed sid* drops the row, on
    purpose: a valuation with no instrument attached is not worth storing.
    """
    if not isinstance(payload, list):
        return []

    rows: list[_Row] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        sid = str(item.get(_TPEX_FIELDS["sid"]) or "").strip()
        if not sid:
            continue
        rows.append(
            _Row(
                sid=sid,
                date=_roc_date(item.get(_TPEX_FIELDS["date"])) or fallback,
                pe_ratio=_to_float(item.get(_TPEX_FIELDS["pe"])),
                pb_ratio=_to_float(item.get(_TPEX_FIELDS["pb"])),
                dividend_yield=_to_float(item.get(_TPEX_FIELDS["yield"])),
                source="tpex",
            )
        )
    return rows


def _upsert(db: Session, rows: list[_Row]) -> int:
    if not rows:
        return 0
    payload = [
        {
            "sid": row.sid,
            "date": row.date,
            "pe_ratio": row.pe_ratio,
            "pb_ratio": row.pb_ratio,
            "dividend_yield": row.dividend_yield,
            "source": row.source,
        }
        for row in rows
    ]
    stmt = pg_insert(ValuationDay).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ValuationDay.sid, ValuationDay.date],
        set_={
            c: stmt.excluded[c]
            for c in ("pe_ratio", "pb_ratio", "dividend_yield", "source")
        },
    )
    db.execute(stmt)
    return len(payload)


def _stamp(db: Session, source: str, bucket: str, count: int) -> None:
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


def _is_stale(log: FundamentalsFetchLog, now: datetime.datetime) -> bool:
    """Today's snapshot expires; a past session's never does.

    A ratio published for a closed session is final. Today's moves with the
    close, and an empty one fetched at 09:30 is "not published yet" rather
    than "this board has no valuations".
    """
    if log.row_count > 0 and log.bucket != now.date().isoformat():
        return False
    fetched_at = log.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    return (now - fetched_at).total_seconds() > settings.current_month_ttl_seconds


def refresh(db: Session, *, force: bool = False) -> dict[str, int]:
    """Pull today's board from both exchanges. One request each.

    `skipped` in the job above means both snapshots were already stamped fresh,
    which on a weekend or a second run within the TTL is the expected answer.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    bucket = now.date().isoformat()

    logs = {
        log.source: log
        for log in db.execute(
            select(FundamentalsFetchLog).where(
                FundamentalsFetchLog.source.in_((TWSE_SOURCE, TPEX_SOURCE)),
                FundamentalsFetchLog.bucket == bucket,
            )
        ).scalars()
    }

    stats = {"fetched": 0, "cached": 0, "rows": 0}
    for source, url, parse in (
        (TWSE_SOURCE, TWSE_URL, parse_twse),
        (TPEX_SOURCE, TPEX_URL, parse_tpex),
    ):
        log = logs.get(source)
        if not force and log is not None and not _is_stale(log, now):
            stats["cached"] += 1
            continue

        rows = parse(_get_json(url), now.date())
        stats["rows"] += _upsert(db, rows)
        _stamp(db, source, bucket, len(rows))
        stats["fetched"] += 1
        db.commit()

    return stats


def read_recent(db: Session, sid: str, days: int) -> list[ValuationDay]:
    """The newest `days` stored sessions for one stock, newest first. Never fetches.

    The band half of Chen's 買得便宜: a PE means little on its own and a lot
    against what this same company has traded at. Cache-only for the reason
    `latest` is -- the daily job fills the whole market from one board-wide
    report, so a thin history here means the job is young, not that this stock
    is unusual, and the caller reports that as a coverage gap either way.
    """
    return list(
        db.scalars(
            select(ValuationDay)
            .where(ValuationDay.sid == sid)
            .order_by(ValuationDay.date.desc())
            .limit(days)
        )
    )


def latest(db: Session, sid: str) -> ValuationDay | None:
    """The newest stored session for one stock. Never fetches.

    Cache-only for the same reason `/analysis/chen` is: it backs a card anyone
    can open, and the daily job is what keeps it current.
    """
    return db.execute(
        select(ValuationDay)
        .where(ValuationDay.sid == sid)
        .order_by(ValuationDay.date.desc())
        .limit(1)
    ).scalar_one_or_none()
