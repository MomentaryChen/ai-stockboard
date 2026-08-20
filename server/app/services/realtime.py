"""Realtime quotes from the TWSE MIS endpoint.

Uses twstock.realtime for the request and field mapping, but reads the raw
payload so we can also surface `y` (yesterday's close), which the library's
formatter drops -- without it the UI cannot show change / change %.

Realtime data is intentionally NOT persisted: it is superseded within seconds
and the daily close already lands in `daily_price` via the history service.
"""

import logging

import twstock
from twstock import realtime as tw_realtime

from app.schemas import RealtimeQuote, RealtimeResponse
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_list(values) -> list[float]:
    if not values:
        return []
    return [v for v in (_to_float(x) for x in values) if v is not None]


def _int_list(values) -> list[int]:
    if not values:
        return []
    return [v for v in (_to_int(x) for x in values) if v is not None]


def _build_quote(entry: dict) -> RealtimeQuote:
    formatted = tw_realtime._format_stock_info(entry)
    info = formatted["info"]
    rt = formatted["realtime"]

    last = _to_float(rt["latest_trade_price"])
    yesterday = _to_float(entry.get("y"))

    change = change_pct = None
    if last is not None and yesterday:
        change = round(last - yesterday, 2)
        change_pct = round(change / yesterday * 100, 2)

    return RealtimeQuote(
        code=info["code"],
        name=info["name"],
        fullname=info["fullname"],
        time=info["time"],
        timestamp=formatted["timestamp"],
        open=_to_float(rt["open"]),
        high=_to_float(rt["high"]),
        low=_to_float(rt["low"]),
        latest_trade_price=last,
        trade_volume=_to_int(rt["trade_volume"]),
        accumulate_trade_volume=_to_int(rt["accumulate_trade_volume"]),
        yesterday_close=yesterday,
        change=change,
        change_percent=change_pct,
        best_bid_price=_float_list(rt["best_bid_price"]),
        best_bid_volume=_int_list(rt["best_bid_volume"]),
        best_ask_price=_float_list(rt["best_ask_price"]),
        best_ask_volume=_int_list(rt["best_ask_volume"]),
    )


def get_quotes(sids: list[str]) -> RealtimeResponse:
    known = [s for s in sids if s in twstock.codes]
    errors = {s: "Unknown stock ID" for s in sids if s not in twstock.codes}

    if not known:
        return RealtimeResponse(
            success=False, message="No valid stock ID", quotes=[], errors=errors
        )

    data = {}
    for attempt in range(MAX_ATTEMPTS):
        twse_throttle.acquire()
        try:
            data = tw_realtime.get_raw(known)
        except Exception:
            logger.warning("realtime fetch failed attempt=%d", attempt + 1, exc_info=True)
            continue
        # 5000 == JSON decode error, usually means we polled too fast.
        if data.get("rtcode") != "5000":
            break

    if data.get("rtcode") == "5000" or "msgArray" not in data:
        return RealtimeResponse(
            success=False,
            message=data.get("rtmessage", "Upstream request failed"),
            quotes=[],
            errors=errors,
        )

    entries = [e for e in data["msgArray"] if "tlong" in e]
    quotes = [_build_quote(e) for e in entries]

    returned = {q.code for q in quotes}
    for sid in known:
        if sid not in returned:
            errors[sid] = "No realtime data (market closed or not traded today)"

    return RealtimeResponse(
        success=bool(quotes),
        message=None if quotes else data.get("rtmessage", "Empty query"),
        quotes=quotes,
        errors=errors,
    )
