"""Realtime quotes from the TWSE MIS endpoint.

Uses twstock.realtime for the request and field mapping, but reads the raw
payload so we can also surface `y` (yesterday's close), which the library's
formatter drops -- without it the UI cannot show change / change %.

Realtime data is intentionally NOT persisted: it is superseded within seconds
and the daily close already lands in `daily_price` via the history service.
"""

import logging
import time

from twstock import realtime as tw_realtime
from twstock.proxy import get_proxies, get_session

from app.schemas import RealtimeQuote, RealtimeResponse
from app.services import codes as codes_service
from app.services import market_index
from app.throttle import twse_throttle

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def _channel(sid: str) -> str:
    """MIS channel name for a quote.

    twstock's own helper derives the tse_/otc_ prefix from its bundled listing,
    which has no row for an index and so mislabels 大盤 as OTC. Indices carry
    their channel in the registry; everything else is decided by the exchange
    recorded in `stock_code`. An unknown sid never reaches here -- get_quotes
    filters it out first -- so the otc default is only a fallback.
    """
    meta = market_index.get(sid)
    if meta is not None:
        return meta.channel
    info = codes_service.get_stock(sid)
    prefix = "tse" if info is not None and info.data_source == "twse" else "otc"
    return f"{prefix}_{sid}.tw"


def _fetch_raw(sids: list[str]) -> dict:
    """twstock.realtime.get_raw, but with our channel mapping."""
    session = get_session()
    session.get(tw_realtime.SESSION_URL, proxies=get_proxies())
    url = tw_realtime.STOCKINFO_URL.format(
        stock_id="|".join(_channel(s) for s in sids),
        time=int(time.time()) * 1000,
    )
    try:
        return session.get(url, proxies=get_proxies()).json()
    except ValueError:
        return {"rtmessage": "json decode error", "rtcode": "5000"}


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


# Last actual print (MIS `z` / `pz`) seen this session. The book is not stored
# here -- that is a live quote, not a trade. Keyed by (code, YYYYMMDD) so a
# Monday poll does not resurrect Friday's last tick as if it just printed.
_last_prints: dict[tuple[str, str], float] = {}


def _session_price(code: str, day: str, rt: dict, entry: dict) -> float | None:
    """現價 for a MIS snapshot.

    `z` is the print *in this 5-second snapshot*, not the last trade of the
    session. Continuous matching (since 2020) means most snapshots have no
    print, so `z` comes back as '-' and a naive float() is None -- which is
    why the board showed '--' all morning while the book, open, high and
    low were live.

    Order of preference:
      1. this snapshot's print (`z`)
      2. the feed's previous print (`pz`)
      3. the last print we ourselves observed today (the 10s poll catches
         most of them even when this call's window is empty)
      4. best bid, then best ask -- what TWSE's own 五檔 page falls back to
         when you just need a number on the board
    """
    printed = _to_float(rt["latest_trade_price"]) or _to_float(entry.get("pz"))
    if printed is not None:
        stale = [key for key in _last_prints if key[1] != day]
        for key in stale:
            del _last_prints[key]
        _last_prints[(code, day)] = printed
        return printed

    cached = _last_prints.get((code, day))
    if cached is not None:
        return cached

    bids = _float_list(rt["best_bid_price"])
    if bids:
        return bids[0]
    asks = _float_list(rt["best_ask_price"])
    if asks:
        return asks[0]
    return None


def _build_quote(entry: dict) -> RealtimeQuote:
    # Index payloads omit "nf" (full name) entirely and twstock's formatter
    # subscripts it directly, so fill the gap before handing the entry over.
    formatted = tw_realtime._format_stock_info({"nf": None, **entry})
    info = formatted["info"]
    rt = formatted["realtime"]

    last = _session_price(info["code"], str(entry.get("d") or ""), rt, entry)
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
    def _known(sid: str) -> bool:
        # Resolves indices and delisted codes too -- MIS simply returns no
        # entry for the latter, which becomes a per-code error below.
        return codes_service.get_stock(sid) is not None

    known = [s for s in sids if _known(s)]
    errors = {s: "Unknown stock ID" for s in sids if not _known(s)}

    if not known:
        return RealtimeResponse(
            success=False, message="No valid stock ID", quotes=[], errors=errors
        )

    data = {}
    for attempt in range(MAX_ATTEMPTS):
        twse_throttle.acquire()
        try:
            data = _fetch_raw(known)
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
