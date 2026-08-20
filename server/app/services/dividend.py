"""Ex-right / ex-dividend events, served database-first.

TWSE's TWT49U report accepts a calendar-year range and returns every listed
name that went ex in that year, so one throttled request fills the table for
the whole board. TWT48U (the announcement calendar) is merged on top of the
current year so upcoming dates show up before they trade.

TPEX does not publish an equivalent archive: `exDailyQ` is the current window
and `tpex_exright_prepost` is the announcement calendar. Those two snapshots
are cached as a single `live` bucket and accumulate in `dividend_event` over
time, which is why OTC coverage is `recent` rather than `history`.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from twstock.proxy import get_proxies, get_session

from app.config import get_settings
from app.models import DailyPrice, DividendEvent, DividendFetchLog
from app.services import codes as codes_service
from app.services import market_index
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

TWSE_RESULT_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
TWSE_PREVIEW_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U"
TPEX_DAILY_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
TPEX_PREVIEW_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"

MAX_ATTEMPTS = 3
TIMEOUT = 30
TPEX_LIVE_BUCKET = "live"

_KIND_MAP = {
    "息": "息",
    "權": "權",
    "權息": "權息",
    "除息": "息",
    "除權": "權",
    "除權息": "權息",
}
_ROC_YMD = re.compile(r"(\d+)年(\d+)月(\d+)日")


@dataclass(frozen=True, slots=True)
class _Row:
    sid: str
    ex_date: datetime.date
    name: str
    kind: str
    close_before: float | None
    reference_price: float | None
    deduction: float | None
    cash_dividend: float | None
    stock_dividend: float | None
    source: str


def _to_float(text: object) -> float | None:
    if text is None:
        return None
    raw = str(text).strip().replace(",", "")
    if not raw or raw in {"--", "N/A", "n/a"} or "<" in raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _kind(text: object) -> str:
    return _KIND_MAP.get(str(text).strip(), str(text).strip())


def parse_ex_date(text: object) -> datetime.date | None:
    """TWSE '113年03月18日', TPEX '115/08/20' or compact '1150811'."""
    if text is None:
        return None
    raw = str(text).strip()
    matched = _ROC_YMD.search(raw)
    if matched:
        year, month, day = (int(g) for g in matched.groups())
        return datetime.date(year + 1911, month, day)
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 3:
            try:
                return datetime.date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
            except ValueError:
                return None
    digits = raw.replace("/", "")
    if digits.isdigit() and len(digits) in (6, 7):
        year_len = len(digits) - 4
        try:
            return datetime.date(
                int(digits[:year_len]) + 1911,
                int(digits[year_len : year_len + 2]),
                int(digits[year_len + 2 :]),
            )
        except ValueError:
            return None
    return None


def _cash_for_result(kind: str, deduction: float | None, cash: float | None) -> float | None:
    """Prefer an explicit cash column; for 息-only TWSE rows the deduction is it."""
    if cash:
        return cash
    if kind == "息":
        return deduction
    return None


def year_range(years: int, today: datetime.date | None = None) -> list[int]:
    today = today or datetime.date.today()
    return list(range(today.year - years + 1, today.year + 1))


def _is_stale(log: DividendFetchLog, bucket: str, now: datetime.datetime) -> bool:
    today = now.date()
    is_live = bucket == TPEX_LIVE_BUCKET or (
        bucket.isdigit() and int(bucket) == today.year
    )
    if not is_live and log.row_count > 0:
        return False

    fetched_at = log.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    return (now - fetched_at).total_seconds() > settings.current_month_ttl_seconds


def _get_json(url: str, params: dict | None = None) -> object:
    session = get_session()
    for attempt in range(MAX_ATTEMPTS):
        twse_throttle.acquire()
        try:
            response = session.get(
                url, params=params or {}, proxies=get_proxies(), timeout=TIMEOUT
            )
            return response.json()
        except Exception:
            logger.warning(
                "dividend fetch failed url=%s attempt=%d", url, attempt + 1, exc_info=True
            )
    return None


def _parse_twse_result(payload: object) -> list[_Row]:
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    rows: list[_Row] = []
    for item in payload.get("data") or []:
        if len(item) < 7:
            continue
        ex_date = parse_ex_date(item[0])
        sid = str(item[1]).strip()
        if ex_date is None or not sid:
            continue
        kind = _kind(item[6])
        deduction = _to_float(item[5])
        rows.append(
            _Row(
                sid=sid,
                ex_date=ex_date,
                name=str(item[2]).strip(),
                kind=kind,
                close_before=_to_float(item[3]),
                reference_price=_to_float(item[4]),
                deduction=deduction,
                cash_dividend=_cash_for_result(kind, deduction, None),
                stock_dividend=None,
                source="twse",
            )
        )
    return rows


def _parse_twse_preview(payload: object) -> list[_Row]:
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    rows: list[_Row] = []
    for item in payload.get("data") or []:
        if len(item) < 8:
            continue
        ex_date = parse_ex_date(item[0])
        sid = str(item[1]).strip()
        if ex_date is None or not sid:
            continue
        kind = _kind(item[3])
        cash = _to_float(item[7])
        stock = _to_float(item[4])
        rows.append(
            _Row(
                sid=sid,
                ex_date=ex_date,
                name=str(item[2]).strip(),
                kind=kind,
                close_before=None,
                reference_price=None,
                deduction=cash,
                cash_dividend=_cash_for_result(kind, cash, cash),
                stock_dividend=stock if stock else None,
                source="twse",
            )
        )
    return rows


def _parse_tpex_daily(payload: object) -> list[_Row]:
    tables = payload.get("tables") if isinstance(payload, dict) else None
    data = (tables[0].get("data") if tables else None) or []
    rows: list[_Row] = []
    for item in data:
        if len(item) < 14:
            continue
        ex_date = parse_ex_date(item[0])
        sid = str(item[1]).strip()
        if ex_date is None or not sid:
            continue
        kind = _kind(item[8])
        deduction = _to_float(item[7])
        cash = _to_float(item[13])
        stock = _to_float(item[14]) if len(item) > 14 else None
        rows.append(
            _Row(
                sid=sid,
                ex_date=ex_date,
                name=str(item[2]).strip(),
                kind=kind,
                close_before=_to_float(item[3]),
                reference_price=_to_float(item[4]),
                deduction=deduction,
                cash_dividend=_cash_for_result(kind, deduction, cash),
                stock_dividend=stock if stock else None,
                source="tpex",
            )
        )
    return rows


def _parse_tpex_preview(payload: object) -> list[_Row]:
    items = payload if isinstance(payload, list) else []
    rows: list[_Row] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ex_date = parse_ex_date(item.get("ExRrightsExDividendDate"))
        sid = str(item.get("SecuritiesCompanyCode") or "").strip()
        if ex_date is None or not sid:
            continue
        kind = _kind(item.get("ExRrightsExDividend"))
        cash = _to_float(item.get("CashDividend"))
        stock = _to_float(item.get("StockDividendRatio"))
        rows.append(
            _Row(
                sid=sid,
                ex_date=ex_date,
                name=str(item.get("CompanyName") or "").strip(),
                kind=kind,
                close_before=None,
                reference_price=None,
                deduction=cash,
                cash_dividend=_cash_for_result(kind, cash, cash),
                stock_dividend=stock if stock else None,
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
            "ex_date": row.ex_date,
            "name": row.name[:64],
            "kind": row.kind[:8],
            "close_before": row.close_before,
            "reference_price": row.reference_price,
            "deduction": row.deduction,
            "cash_dividend": row.cash_dividend,
            "stock_dividend": row.stock_dividend,
            "source": row.source,
        }
        for row in rows
    ]
    stmt = pg_insert(DividendEvent).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DividendEvent.sid, DividendEvent.ex_date],
        set_={
            c: stmt.excluded[c]
            for c in (
                "name",
                "kind",
                "close_before",
                "reference_price",
                "deduction",
                "cash_dividend",
                "stock_dividend",
                "source",
            )
        },
    )
    db.execute(stmt)
    return len(payload)


def _log_fetch(db: Session, source: str, bucket: str, count: int) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = pg_insert(DividendFetchLog).values(
        source=source, bucket=bucket, row_count=count, fetched_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DividendFetchLog.source, DividendFetchLog.bucket],
        set_={"row_count": stmt.excluded.row_count, "fetched_at": stmt.excluded.fetched_at},
    )
    db.execute(stmt)


def _fetch_twse_year(year: int) -> list[_Row]:
    start = f"{year}0101"
    end = f"{year}1231"
    result = _parse_twse_result(
        _get_json(TWSE_RESULT_URL, {"response": "json", "startDate": start, "endDate": end})
    )
    today = datetime.date.today()
    if year == today.year:
        # Preview first so the yearly result overwrites the same date with
        # close/reference prices once the name has actually gone ex.
        preview = _parse_twse_preview(_get_json(TWSE_PREVIEW_URL, {"response": "json"}))
        by_key = {(row.sid, row.ex_date): row for row in preview}
        by_key.update({(row.sid, row.ex_date): row for row in result})
        return list(by_key.values())
    return result


def _fetch_tpex_live() -> list[_Row]:
    preview = _parse_tpex_preview(_get_json(TPEX_PREVIEW_URL))
    daily = _parse_tpex_daily(_get_json(TPEX_DAILY_URL, {"response": "json"}))
    by_key = {(row.sid, row.ex_date): row for row in preview}
    by_key.update({(row.sid, row.ex_date): row for row in daily})
    return list(by_key.values())


def _ensure_twse(db: Session, years: list[int], force: bool) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    logs = {
        log.bucket: log
        for log in db.execute(
            select(DividendFetchLog).where(DividendFetchLog.source == "twse")
        ).scalars()
    }
    for year in years:
        bucket = str(year)
        log = logs.get(bucket)
        if not force and log is not None and not _is_stale(log, bucket, now):
            continue
        rows = _fetch_twse_year(year)
        _upsert(db, rows)
        _log_fetch(db, "twse", bucket, len(rows))
        db.commit()


def _ensure_tpex(db: Session, force: bool) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    log = db.execute(
        select(DividendFetchLog).where(
            DividendFetchLog.source == "tpex",
            DividendFetchLog.bucket == TPEX_LIVE_BUCKET,
        )
    ).scalar_one_or_none()
    if not force and log is not None and not _is_stale(log, TPEX_LIVE_BUCKET, now):
        return
    rows = _fetch_tpex_live()
    _upsert(db, rows)
    _log_fetch(db, "tpex", TPEX_LIVE_BUCKET, len(rows))
    db.commit()


def _latest_close(db: Session, sid: str) -> float | None:
    value = db.execute(
        select(DailyPrice.close)
        .where(DailyPrice.sid == sid, DailyPrice.close.is_not(None))
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(value) if value is not None else None


def get_dividends(
    db: Session, sid: str, years: int, force: bool = False
) -> tuple[list[DividendEvent], str, float | None]:
    """Return (events, coverage, latest_close). Coverage is none for indices."""
    if market_index.is_index(sid):
        return [], "none", None

    info = codes_service.get_stock(sid)
    source = info.data_source if info is not None else "twse"
    years_list = year_range(years)

    if source == "tpex":
        _ensure_tpex(db, force=force)
        coverage = "recent"
    else:
        _ensure_twse(db, years_list, force=force)
        coverage = "history"

    start = datetime.date(years_list[0], 1, 1)
    events = list(
        db.execute(
            select(DividendEvent)
            .where(DividendEvent.sid == sid, DividendEvent.ex_date >= start)
            .order_by(DividendEvent.ex_date.desc())
        ).scalars()
    )
    return events, coverage, _latest_close(db, sid)
