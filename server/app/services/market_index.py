"""台股大盤指數（發行量加權股價指數，TAIEX）.

twstock only knows how to fetch individual stocks -- an index is not in the
listed-company database and its daily history lives behind different TWSE
endpoints. This module fills both gaps so the rest of the service can treat the
index as just another `sid`:

  * `INDICES` makes the index look like a row from twstock's code table, so
    `/api/stocks/t00`, `/history` and `/analysis/traditional` work unchanged.
  * `IndexFetcher.fetch()` has the same signature and return shape as twstock's
    `TWSEFetcher.fetch()`, so `history_service` can cache index days in
    `daily_price` exactly like stock days.

Two upstream reports are merged, because neither is complete on its own:

  MI_5MINS_HIST   日期 / 開盤 / 最高 / 最低 / 收盤指數
  FMTQIK          日期 / 成交股數 / 成交金額 / 成交筆數 / 收盤指數 / 漲跌點數

MI_5MINS_HIST carries the OHLC the candlestick chart needs; FMTQIK carries the
volume the 四大買賣點 rules read. Both return one month per request.
"""

import datetime
import logging
from dataclasses import dataclass

from twstock.proxy import get_proxies, get_session
from twstock.stock import DATATUPLE

from app.throttle import twse_throttle

logger = logging.getLogger(__name__)

OHLC_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
VOLUME_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"

MAX_ATTEMPTS = 3
TIMEOUT = 30


@dataclass(frozen=True)
class IndexMeta:
    """Enough of twstock's code-table row shape to satisfy `StockInfo`."""

    code: str
    name: str
    fullname: str
    #  MIS realtime channel. twstock derives this from the listed-company table,
    #  which has no entry for an index, so it is spelled out here.
    channel: str
    market: str
    start: str
    data_source: str = "twse"
    type: str = "指數"
    group: str = "大盤"
    #  Extra search terms: nobody types "t00" to find the market index.
    aliases: tuple[str, ...] = ()


#  t00 is the TAIEX. Kept as a dict so 櫃買指數 (o00) can be added once its
#  history endpoints are wired up -- realtime already works for both.
INDICES: dict[str, IndexMeta] = {
    "t00": IndexMeta(
        code="t00",
        name="加權指數",
        fullname="發行量加權股價指數",
        channel="tse_t00.tw",
        market="上市",
        start="1966/01/05",  # TAIEX base date
        aliases=("大盤", "台股大盤", "台股", "TAIEX", "指數"),
    ),
}

#: The index the dashboard opens on.
DEFAULT_INDEX = "t00"


def get(sid: str) -> IndexMeta | None:
    return INDICES.get(sid)


def is_index(sid: str) -> bool:
    return sid in INDICES


def matches(meta: IndexMeta, query: str) -> bool:
    q = query.strip()
    if not q:
        return False
    lowered = q.lower()
    return (
        meta.code.lower().startswith(lowered)
        or q in meta.name
        or q in meta.fullname
        or any(lowered in alias.lower() for alias in meta.aliases)
    )


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", "").strip())
    except (AttributeError, ValueError):
        return None


def _to_int(text: str) -> int | None:
    value = _to_float(text)
    return int(value) if value is not None else None


def _roc_date(text: str) -> datetime.datetime | None:
    """'115/08/03' -> datetime(2026, 8, 3). Same convention twstock uses."""
    try:
        year, month, day = text.strip().split("/")
        return datetime.datetime(int(year) + 1911, int(month), int(day))
    except (AttributeError, ValueError):
        return None


class IndexFetcher:
    """Mirrors `twstock.stock.TWSEFetcher` for an index code.

    The caller (history service) throttles the first upstream request; the
    second one is throttled here so both count against the TWSE limiter.
    """

    def fetch(self, year: int, month: int, sid: str, retry: int = 1) -> dict:
        meta = get(sid)
        if meta is None:
            return {"stat": "unknown index", "data": []}

        date = "%d%02d01" % (year, month)

        ohlc = self._get(OHLC_URL, date, retry)
        twse_throttle.acquire()
        volume = self._get(VOLUME_URL, date, retry)

        return {"stat": "OK", "data": self._merge(ohlc, volume)}

    def _get(self, url: str, date: str, retry: int) -> list[list[str]]:
        session = get_session()
        for _ in range(max(retry, 1)):
            try:
                response = session.get(
                    url,
                    params={"date": date, "response": "json"},
                    proxies=get_proxies(),
                    timeout=TIMEOUT,
                )
                payload = response.json()
            except Exception:
                logger.warning("index fetch failed url=%s date=%s", url, date, exc_info=True)
                continue
            if payload.get("stat") == "OK":
                return payload.get("data") or []
            # Months before the index existed, or a holiday-only month.
            logger.info("index report empty url=%s date=%s stat=%s", url, date, payload.get("stat"))
            return []
        return []

    @staticmethod
    def _merge(ohlc: list[list[str]], volume: list[list[str]]) -> list:
        """OHLC drives the row set; volume columns are joined on by date."""
        by_date = {}
        for row in volume:
            #  日期, 成交股數, 成交金額, 成交筆數, 收盤指數, 漲跌點數
            if len(row) < 6:
                continue
            day = _roc_date(row[0])
            if day is not None:
                by_date[day] = row

        merged = []
        for row in ohlc:
            #  日期, 開盤, 最高, 最低, 收盤
            if len(row) < 5:
                continue
            day = _roc_date(row[0])
            if day is None:
                continue
            vol = by_date.get(day)
            merged.append(
                DATATUPLE(
                    date=day,
                    capacity=_to_int(vol[1]) if vol else None,
                    turnover=_to_int(vol[2]) if vol else None,
                    open=_to_float(row[1]),
                    high=_to_float(row[2]),
                    low=_to_float(row[3]),
                    close=_to_float(row[4]),
                    change=_to_float(vol[5]) if vol else None,
                    transaction=_to_int(vol[3]) if vol else None,
                    note="",
                )
            )
        return merged
