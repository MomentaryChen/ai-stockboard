"""Institutional net buying and margin balances, served database-first.

This is deliberately not an input to 四大買賣點. The two answers different
questions -- oversold turn vs who was buying -- and grafting one onto the
other would make the backtest score a different rule than the card shows.

The exchanges publish these as *all-market daily* reports, not per-stock
monthlies like STOCK_DAY. One T86 call is every listed name for one session,
so the cache is stamped per (source, report, date): the second stock to ask
for 2026-08-20 is free. Past dates with rows never re-fetch; empty results
and the current session expire on CURRENT_MONTH_TTL_SECONDS, because an empty
T86 at 14:00 is "not published yet", not "nobody traded".

A stock page is capped on how many upstream calls it may spend, and
institutional reports are fetched before margin so a first visit can still
show a 買超 streak. The nightly job warms the last few sessions for both
markets so the common case is a cache hit.
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
from app.models import ChipDay, ChipFetchLog, DailyPrice
from app.services import codes as codes_service
from app.services import market_index
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)
settings = get_settings()

TWSE_INST_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_INST_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
)
TPEX_MARGIN_URL = (
    "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/"
    "margin_bal_result.php"
)

REPORT_INST = "inst"
REPORT_MARGIN = "margin"

# T86 columns 0..18 as published since the 外資自營商 split. A shorter row is
# skipped rather than guessed -- an upstream reshape must fail the parser tests
# instead of silently swapping 投信 with 自營.
T86_MIN_COLS = 19
# MI_MARGN stock table: 代號 .. 融券今日餘額 lives at index 12.
MARGN_MIN_COLS = 13
# TPEX 3insti: 代號, 名稱, then seven buy/sell/net groups, then 合計.
TPEX_INST_MIN_COLS = 24
# TPEX margin: 券餘額 lives at index 14.
TPEX_MARGIN_MIN_COLS = 15

DEFAULT_DAYS = 10
MAX_DAYS = 15
ANONYMOUS_MAX_DAYS = 10
# Six throttled calls is ~11s. Inst is queued first so a cold page still
# answers "外資連買幾日" even if 融資 has to wait for the next load.
MAX_UPSTREAM_CALLS_PER_REQUEST = 6
# Nightly warmer. 5 sessions × 2 reports × 2 markets = 20 calls, ~37s.
JOB_DAYS = 5

MAX_ATTEMPTS = 3
TIMEOUT = 30

CALENDAR_SID = market_index.DEFAULT_INDEX


@dataclass(frozen=True, slots=True)
class InstRow:
    sid: str
    foreign_net: int
    trust_net: int
    dealer_net: int
    total_net: int


@dataclass(frozen=True, slots=True)
class MarginRow:
    sid: str
    margin_balance: int
    margin_change: int
    short_balance: int
    short_change: int


@dataclass(frozen=True, slots=True)
class ChipFlow:
    streak: str  # buy | sell | none
    streak_days: int
    net_5d: int | None


def _to_int(text: object) -> int | None:
    if text is None:
        return None
    raw = str(text).strip().replace(",", "").replace("+", "")
    if not raw or raw in {"--", "---", "N/A", "n/a"}:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _roc_slash(day: datetime.date) -> str:
    return f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"


def streak_and_net(values: list[int | None], window: int = 5) -> ChipFlow:
    """Consecutive same-sign days from the newest value, plus a trailing sum.

    Zeros and missing figures break a streak (they are not a buy and not a
    sell). Leading holes are skipped so a date that has 融資 but not yet 法人
    does not hide the streak behind it.
    """
    sign: int | None = None
    days = 0
    for value in values:
        if value is None or value == 0:
            if days:
                break
            continue
        this = 1 if value > 0 else -1
        if sign is None:
            sign = this
            days = 1
        elif this == sign:
            days += 1
        else:
            break

    sample = [v for v in values[:window] if v is not None]
    net_5d = sum(sample) if sample else None
    if days == 0 or sign is None:
        return ChipFlow(streak="none", streak_days=0, net_5d=net_5d)
    return ChipFlow(
        streak="buy" if sign > 0 else "sell",
        streak_days=days,
        net_5d=net_5d,
    )


def parse_t86(payload: object) -> list[InstRow]:
    """TWSE T86: 外陸資 (ex-dealer) + 外資自營商 = 外資, plus 投信 / 自營 / 合計."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    rows: list[InstRow] = []
    for item in payload.get("data") or []:
        if not isinstance(item, (list, tuple)) or len(item) < T86_MIN_COLS:
            continue
        sid = str(item[0]).strip()
        if not sid:
            continue
        foreign_ex = _to_int(item[4])
        foreign_dealer = _to_int(item[7])
        trust = _to_int(item[10])
        dealer = _to_int(item[11])
        total = _to_int(item[18])
        if None in (foreign_ex, foreign_dealer, trust, dealer, total):
            continue
        rows.append(
            InstRow(
                sid=sid,
                foreign_net=foreign_ex + foreign_dealer,
                trust_net=trust,
                dealer_net=dealer,
                total_net=total,
            )
        )
    return rows


def parse_mi_margn(payload: object) -> list[MarginRow]:
    """TWSE MI_MARGN stock table. tables[0] is the market summary; skip it."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    table = _stock_table(payload.get("tables") or [])
    if table is None:
        return []
    rows: list[MarginRow] = []
    for item in table:
        if not isinstance(item, (list, tuple)) or len(item) < MARGN_MIN_COLS:
            continue
        sid = str(item[0]).strip()
        if not sid:
            continue
        margin_prev = _to_int(item[5])
        margin_today = _to_int(item[6])
        short_prev = _to_int(item[11])
        short_today = _to_int(item[12])
        if None in (margin_prev, margin_today, short_prev, short_today):
            continue
        rows.append(
            MarginRow(
                sid=sid,
                margin_balance=margin_today,
                margin_change=margin_today - margin_prev,
                short_balance=short_today,
                short_change=short_today - short_prev,
            )
        )
    return rows


def _stock_table(tables: object) -> list | None:
    if not isinstance(tables, list):
        return None
    for table in tables:
        if not isinstance(table, dict):
            continue
        fields = table.get("fields") or []
        data = table.get("data") or []
        if not data or not fields:
            continue
        header = str(fields[0])
        if "代號" in header or header in {"證券代號", "代號"}:
            return data
    return None


def parse_tpex_inst(payload: object) -> list[InstRow]:
    """TPEX 3insti daily: 外資合計 / 投信 / 自營商合計 / 三大法人合計."""
    table = _tpex_table(payload)
    if table is None:
        return []
    rows: list[InstRow] = []
    for item in table:
        if not isinstance(item, (list, tuple)) or len(item) < TPEX_INST_MIN_COLS:
            continue
        sid = str(item[0]).strip()
        if not sid:
            continue
        foreign = _to_int(item[10])
        trust = _to_int(item[13])
        dealer = _to_int(item[22])
        total = _to_int(item[23])
        if None in (foreign, trust, dealer, total):
            continue
        rows.append(
            InstRow(
                sid=sid,
                foreign_net=foreign,
                trust_net=trust,
                dealer_net=dealer,
                total_net=total,
            )
        )
    return rows


def parse_tpex_margin(payload: object) -> list[MarginRow]:
    table = _tpex_table(payload)
    if table is None:
        return []
    rows: list[MarginRow] = []
    for item in table:
        if not isinstance(item, (list, tuple)) or len(item) < TPEX_MARGIN_MIN_COLS:
            continue
        sid = str(item[0]).strip()
        if not sid:
            continue
        margin_prev = _to_int(item[2])
        margin_today = _to_int(item[6])
        short_prev = _to_int(item[10])
        short_today = _to_int(item[14])
        if None in (margin_prev, margin_today, short_prev, short_today):
            continue
        rows.append(
            MarginRow(
                sid=sid,
                margin_balance=margin_today,
                margin_change=margin_today - margin_prev,
                short_balance=short_today,
                short_change=short_today - short_prev,
            )
        )
    return rows


def _tpex_table(payload: object) -> list | None:
    if not isinstance(payload, dict):
        return None
    tables = payload.get("tables") or []
    if not isinstance(tables, list) or not tables:
        return None
    data = tables[0].get("data") if isinstance(tables[0], dict) else None
    return data if isinstance(data, list) else None


def _is_stale(log: ChipFetchLog, day: datetime.date, now: datetime.datetime) -> bool:
    is_today = day == now.date()
    if not is_today and log.row_count > 0:
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
                "chip fetch failed url=%s attempt=%d", url, attempt + 1, exc_info=True
            )
    return None


def _fetch_report(source: str, report: str, day: datetime.date) -> list:
    ymd = day.strftime("%Y%m%d")
    roc = _roc_slash(day)
    if source == "twse" and report == REPORT_INST:
        payload = _get_json(
            TWSE_INST_URL,
            {"date": ymd, "selectType": "ALLBUT0999", "response": "json"},
        )
        return parse_t86(payload)
    if source == "twse" and report == REPORT_MARGIN:
        payload = _get_json(
            TWSE_MARGIN_URL,
            {"date": ymd, "selectType": "STOCK", "response": "json"},
        )
        return parse_mi_margn(payload)
    if source == "tpex" and report == REPORT_INST:
        payload = _get_json(
            TPEX_INST_URL,
            {"l": "zh-tw", "se": "EW", "t": "D", "d": roc, "o": "json"},
        )
        return parse_tpex_inst(payload)
    payload = _get_json(
        TPEX_MARGIN_URL, {"l": "zh-tw", "d": roc, "o": "json"}
    )
    return parse_tpex_margin(payload)


def _stamp(
    db: Session, source: str, report: str, day: datetime.date, row_count: int
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    db.execute(
        pg_insert(ChipFetchLog)
        .values(source=source, report=report, date=day, row_count=row_count, fetched_at=now)
        .on_conflict_do_update(
            index_elements=["source", "report", "date"],
            set_={"row_count": row_count, "fetched_at": now},
        )
    )


def _upsert_inst(db: Session, source: str, day: datetime.date, rows: list[InstRow]) -> int:
    if not rows:
        return 0
    payload = [
        {
            "sid": row.sid,
            "date": day,
            "foreign_net": row.foreign_net,
            "trust_net": row.trust_net,
            "dealer_net": row.dealer_net,
            "total_net": row.total_net,
            "source": source,
        }
        for row in rows
    ]
    stmt = pg_insert(ChipDay).values(payload)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["sid", "date"],
            set_={
                "foreign_net": stmt.excluded.foreign_net,
                "trust_net": stmt.excluded.trust_net,
                "dealer_net": stmt.excluded.dealer_net,
                "total_net": stmt.excluded.total_net,
                "source": stmt.excluded.source,
            },
        )
    )
    return len(rows)


def _upsert_margin(
    db: Session, source: str, day: datetime.date, rows: list[MarginRow]
) -> int:
    if not rows:
        return 0
    payload = [
        {
            "sid": row.sid,
            "date": day,
            "margin_balance": row.margin_balance,
            "margin_change": row.margin_change,
            "short_balance": row.short_balance,
            "short_change": row.short_change,
            "source": source,
        }
        for row in rows
    ]
    stmt = pg_insert(ChipDay).values(payload)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["sid", "date"],
            set_={
                "margin_balance": stmt.excluded.margin_balance,
                "margin_change": stmt.excluded.margin_change,
                "short_balance": stmt.excluded.short_balance,
                "short_change": stmt.excluded.short_change,
                "source": stmt.excluded.source,
            },
        )
    )
    return len(rows)


def _logs_for(
    db: Session, source: str, dates: list[datetime.date]
) -> dict[tuple[str, datetime.date], ChipFetchLog]:
    if not dates:
        return {}
    rows = db.scalars(
        select(ChipFetchLog).where(
            ChipFetchLog.source == source,
            ChipFetchLog.date.in_(dates),
        )
    ).all()
    return {(row.report, row.date): row for row in rows}


def ensure_dates(
    db: Session,
    source: str,
    dates: list[datetime.date],
    *,
    force: bool = False,
    max_calls: int | None = MAX_UPSTREAM_CALLS_PER_REQUEST,
) -> tuple[list[str], list[str], int]:
    """Fetch missing all-market reports. Inst first, then margin.

    `max_calls` bounds a page view; the job passes None. Returns
    (fetched YYYY-MM-DD, cached YYYY-MM-DD, rows upserted).
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    logs = _logs_for(db, source, dates)
    fetched: list[str] = []
    cached: list[str] = []
    upserted = 0
    spent = 0

    def _fresh(report: str, day: datetime.date) -> bool:
        log = logs.get((report, day))
        return (
            not force and log is not None and not _is_stale(log, day, now)
        )

    # All missing inst reports before any margin, newest dates first: a capped
    # page view still shows the recent 買超 streak even if 融資 has to wait.
    inst_pending: list[tuple[str, datetime.date]] = []
    margin_pending: list[tuple[str, datetime.date]] = []
    for day in dates:
        inst_ok = _fresh(REPORT_INST, day)
        margin_ok = _fresh(REPORT_MARGIN, day)
        if inst_ok and margin_ok:
            cached.append(day.isoformat())
            continue
        if not inst_ok:
            inst_pending.append((REPORT_INST, day))
        if not margin_ok:
            margin_pending.append((REPORT_MARGIN, day))
    pending = inst_pending + margin_pending

    for report, day in pending:
        if max_calls is not None and spent >= max_calls:
            break
        rows = _fetch_report(source, report, day)
        spent += 1
        if report == REPORT_INST:
            upserted += _upsert_inst(db, source, day, rows)
        else:
            upserted += _upsert_margin(db, source, day, rows)
        _stamp(db, source, report, day, len(rows))
        key = day.isoformat()
        if key not in fetched:
            fetched.append(key)

    db.commit()
    return fetched, cached, upserted


def trading_dates(db: Session, sid: str, days: int) -> list[datetime.date]:
    """Newest-first trading days we already hold for `sid`."""
    rows = db.scalars(
        select(DailyPrice.date)
        .where(DailyPrice.sid == sid, DailyPrice.close.is_not(None))
        .order_by(DailyPrice.date.desc())
        .limit(days)
    ).all()
    return list(rows)


def calendar_dates(db: Session, days: int) -> list[datetime.date]:
    """TWSE sessions, read off the index bars the market board already keeps."""
    dates = trading_dates(db, CALENDAR_SID, days)
    if dates:
        return dates
    rows = db.scalars(
        select(DailyPrice.date)
        .where(DailyPrice.close.is_not(None))
        .distinct()
        .order_by(DailyPrice.date.desc())
        .limit(days)
    ).all()
    return list(rows)


def refresh_recent(db: Session, *, force: bool = False) -> dict[str, int]:
    """Warm the last `JOB_DAYS` sessions for both markets. Used by the job."""
    dates = calendar_dates(db, JOB_DAYS)
    if not dates:
        return {"fetched": 0, "cached": 0, "rows": 0}
    fetched = cached = rows = 0
    for source in ("twse", "tpex"):
        got, held, upserted = ensure_dates(
            db, source, dates, force=force, max_calls=None
        )
        fetched += len(got)
        cached += len(held)
        rows += upserted
    return {"fetched": fetched, "cached": cached, "rows": rows}


def empty_flow() -> ChipFlow:
    return ChipFlow(streak="none", streak_days=0, net_5d=None)


def flow_for(rows: list[ChipDay], attr: str) -> ChipFlow:
    values = [getattr(row, attr) for row in rows]
    return streak_and_net(values)


def get_chips(
    db: Session,
    sid: str,
    days: int = DEFAULT_DAYS,
    *,
    force: bool = False,
) -> tuple[list[ChipDay], list[str], list[str]]:
    """Ensure then read. Dates come from this stock's own `daily_price` bars."""
    info = codes_service.get_stock(sid)
    if info is None:
        raise KeyError(sid)
    dates = trading_dates(db, sid, days)
    if not dates:
        return [], [], []
    fetched, cached, _ = ensure_dates(db, info.data_source, dates, force=force)
    rows = db.scalars(
        select(ChipDay)
        .where(ChipDay.sid == sid, ChipDay.date.in_(dates))
        .order_by(ChipDay.date.desc())
    ).all()
    return list(rows), fetched, cached
