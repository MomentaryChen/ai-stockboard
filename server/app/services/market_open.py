"""當日開盤情報 -- what one trading day looked like from its open.

The rest of the service answers "where is the price now" and "what do the rules
say". This one answers a different question, the one a trader asks in the first
minutes of a session: *how did the day start, and what has it done since?* Two
numbers carry it:

  gap        open - previous close.  跳空 -- the overnight repricing, which
             happens before a single share changes hands in this session.
  from_open  last - open.            What the session itself has added or given
             back. A day that gaps up 1% and then fades to flat is not the same
             day as one that opens flat and grinds up 1%, and the close alone
             cannot tell them apart.

Both are reported as raw deltas plus directions rather than as a Chinese label,
because the frontend owns user-facing copy in both locales -- see the i18n note
in CLAUDE.md. `gap_direction` x `drift_direction` is what composes 開高走低 and
its eight siblings on the client.

Everything here reads settled daily bars out of PostgreSQL. Today's bar does not
exist until TWSE publishes the day's report (well after the 13:30 close), so an
intraday view of *today* is assembled by the frontend from the realtime quote,
which carries open / high / low / yesterday-close for exactly that reason. This
module is what makes any *other* day answerable, and what takes over for today
once the report lands.
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import DailyPrice
from app.schemas import MoveDirection, OpenSnapshot
from app.services import codes as codes_service
from app.services import history as history_service
from app.services import market_index

#: The exchange's clock. Not `settings.scheduler_timezone`: that one is a
#: deployment preference for when cron-ish jobs fire, whereas "which day is it
#: on the TWSE floor" is a property of the market and must not drift with it.
MARKET_TZ = ZoneInfo("Asia/Taipei")

#: How far back to look for the previous trading day. Comfortably clears the
#: longest closure -- 農曆年 shuts the exchange for about nine days -- so the bar
#: before a post-holiday session is still inside the window.
LOOKBACK_DAYS = 21

#: A gap or drift smaller than this reads as "flat" rather than as a direction.
#: Index levels run to five figures and stock prices to two decimals, so the
#: threshold is on the percentage, not on the raw delta.
FLAT_PCT = 0.05


def market_today() -> datetime.date:
    """Today on the exchange's calendar, whatever the server's clock says."""
    return datetime.datetime.now(MARKET_TZ).date()


def _pct(delta: float | None, base: float | None) -> float | None:
    if delta is None or not base:
        return None
    return round(delta / base * 100, 2)


def _direction(pct: float | None) -> MoveDirection:
    if pct is None:
        return "flat"
    if pct > FLAT_PCT:
        return "up"
    if pct < -FLAT_PCT:
        return "down"
    return "flat"


def _f(value) -> float | None:
    """Numeric(12, 4) comes back as Decimal; the schema wants floats."""
    return None if value is None else float(value)


def _previous_close(bar: DailyPrice, previous: DailyPrice | None) -> float | None:
    """Yesterday's close, from the bar before it or from this bar's own change.

    The previous row is the honest answer and is what we hold in all but one
    case: the requested day is the oldest bar we cached, so there is nothing
    before it. `change` closes that gap -- TWSE reports 漲跌 for stocks and
    漲跌點數 (FMTQIK) for the index, both against the prior close.
    """
    if previous is not None and previous.close is not None:
        return _f(previous.close)
    if bar.close is not None and bar.change is not None:
        return round(_f(bar.close) - _f(bar.change), 4)
    return None


def _snapshot(sid: str, name: str, bar: DailyPrice, previous: DailyPrice | None) -> OpenSnapshot:
    open_ = _f(bar.open)
    close = _f(bar.close)
    prev_close = _previous_close(bar, previous)

    gap = None if open_ is None or prev_close is None else round(open_ - prev_close, 4)
    change = None if close is None or prev_close is None else round(close - prev_close, 4)
    from_open = None if close is None or open_ is None else round(close - open_, 4)

    gap_pct = _pct(gap, prev_close)
    from_open_pct = _pct(from_open, open_)

    return OpenSnapshot(
        sid=sid,
        name=name,
        date=bar.date,
        is_index=market_index.is_index(sid),
        intraday=False,
        open=open_,
        prev_close=prev_close,
        gap=gap,
        gap_percent=gap_pct,
        gap_direction=_direction(gap_pct),
        high=_f(bar.high),
        low=_f(bar.low),
        last=close,
        change=change,
        change_percent=_pct(change, prev_close),
        from_open=from_open,
        from_open_percent=from_open_pct,
        drift_direction=_direction(from_open_pct),
        capacity=bar.capacity,
        turnover=bar.turnover,
    )


def snapshots(
    db: Session, sids: list[str], day: datetime.date
) -> tuple[list[OpenSnapshot], dict[str, str], datetime.date | None]:
    """Open snapshots for `sids` on `day`, plus per-sid failures.

    Cache-only, and deliberately so: this runs on every dashboard load with a
    watchlist attached, and a cold 20-sid list would otherwise queue tens of
    TWSE month-fetches on the same 3-per-5-seconds limiter the realtime poll
    depends on. The batch BFP endpoint made the same call for the same reason.
    A sid with nothing cached comes back in `errors`, not as an upstream trip.

    The third return value is the newest day we hold for the *first* sid (the
    index, as the router orders it) -- what the UI offers when the requested
    date turns out to be a holiday.
    """
    if not sids:
        return [], {}, None

    start = day - datetime.timedelta(days=LOOKBACK_DAYS)
    # `day` bounds the read as well: picking a past date must show that date's
    # session, not leak the rows after it into the "previous bar" lookup.
    bars_by_sid = history_service.read_prices_many(db, sids, start, end=day)

    items: list[OpenSnapshot] = []
    errors: dict[str, str] = {}
    latest: datetime.date | None = None

    for index, sid in enumerate(sids):
        info = codes_service.get_stock(sid)
        if info is None:
            errors[sid] = f"Stock ID '{sid}' not found"
            continue

        bars = bars_by_sid.get(sid, [])
        if index == 0 and bars:
            latest = bars[-1].date

        # read_prices_many orders by date, so the requested session -- when it
        # traded -- is the last row, and the one before it is its predecessor.
        if not bars or bars[-1].date != day:
            errors[sid] = f"No daily bar for '{sid}' on {day.isoformat()}"
            continue

        items.append(_snapshot(sid, info.name, bars[-1], bars[-2] if len(bars) > 1 else None))

    return items, errors, latest
