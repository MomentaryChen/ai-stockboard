"""Taiwan market indices (TAIEX and the TPEx OTC index).

twstock only knows how to fetch individual stocks -- an index is not in the
listed-company database and its daily history lives behind different exchange
endpoints. This module fills both gaps so the rest of the service can treat an
index as just another `sid`:

  * `INDICES` makes the index look like a row from twstock's code table, so
    `/api/stocks/{sid}`, `/history` and `/analysis/traditional` work unchanged.
  * `IndexFetcher.fetch()` has the same signature and return shape as twstock's
    `TWSEFetcher.fetch()`, so `history_service` can cache index days in
    `daily_price` exactly like stock days.

Two upstream reports are merged per index, because neither is complete on its
own. TAIEX (`t00`) uses the TWSE pair; the OTC index (`o00`) uses the TPEx
pair -- the JSON envelopes differ, which is why this fetcher is not a thin
wrapper around `TWSEFetcher`.

  t00  MI_5MINS_HIST   日期 / 開盤 / 最高 / 最低 / 收盤指數
       FMTQIK          日期 / 成交股數 / 成交金額 / 成交筆數 / 收盤指數 / 漲跌點數
  o00  indexInfo/inx   日期 / 開市 / 最高 / 最低 / 收市 / 漲跌
       tradingIndex    日期 / 成交張數 / 金額（仟元） / 筆數 / 櫃買指數 / 漲跌

OHLC carries the candlestick; the volume report carries the 成交 the 四大買賣點
rules read. Both return one month per request. TPEx quotes volume in 張 and
turnover in 仟元, so those two columns are scaled ×1000 before they land in
`daily_price` (股 / 元), the same convention twstock's `TPEXFetcher` uses for
OTC stocks.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from twstock.proxy import get_proxies, get_session
from twstock.stock import DATATUPLE

from app.throttle import twse_throttle

logger = logging.getLogger(__name__)

OHLC_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
VOLUME_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
TPEX_OHLC_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"
TPEX_VOLUME_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex"

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
    ohlc_url: str = OHLC_URL
    volume_url: str = VOLUME_URL
    #  Multiplier applied to the volume report's 成交 and 金額 columns. TWSE
    #  already reports 股 / 元; TPEx reports 張 / 仟元.
    volume_scale: int = 1

    def month_param(self, year: int, month: int) -> str:
        """How this exchange wants the month on the query string.

        TWSE's rwd reports take `20260801`; TPEx's www reports take `2026/08/01`.
        Sending the other form is not a 404 -- it is an empty month, which the
        history cache would then treat as "nothing to fetch".
        """
        if self.data_source == "tpex":
            return f"{year}/{month:02d}/01"
        return f"{year}{month:02d}01"


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
    "o00": IndexMeta(
        code="o00",
        name="櫃買指數",
        fullname="櫃檯買賣中心股價指數",
        channel="otc_o00.tw",
        market="上櫃",
        start="1999/09/01",  # monthly report begins 民國 88/09; base date is 1994
        data_source="tpex",
        ohlc_url=TPEX_OHLC_URL,
        volume_url=TPEX_VOLUME_URL,
        volume_scale=1000,
        aliases=("櫃買", "上櫃指數", "上櫃大盤", "OTC", "TPEx"),
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


def _to_float(text) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    try:
        return float(str(text).replace(",", "").strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _to_int(text) -> int | None:
    value = _to_float(text)
    return int(value) if value is not None else None


def _report_date(text) -> datetime.datetime | None:
    """Parse a report date that may be ROC ('115/08/03') or Gregorian ('2026/08/03').

    TWSE and the TPEx volume report use ROC; the TPEx OHLC report uses
    Gregorian. Trying ROC first would accept 2026/08/03 as year 3937.
    """
    try:
        year, month, day = str(text).strip().split("/")
        y = int(year)
        if y > 1911:
            return datetime.datetime(y, int(month), int(day))
        return datetime.datetime(y + 1911, int(month), int(day))
    except (AttributeError, TypeError, ValueError):
        return None


def _table_rows(payload: dict) -> list:
    """TWSE puts rows on `data`; TPEx nests them in `tables[0].data`."""
    tables = payload.get("tables")
    if tables:
        return tables[0].get("data") or []
    return payload.get("data") or []


class IndexFetcher:
    """Mirrors `twstock.stock.TWSEFetcher` for an index code.

    The caller (history service) throttles the first upstream request; the
    second one is throttled here so both count against the TWSE limiter.
    """

    def fetch(self, year: int, month: int, sid: str, retry: int = 1) -> dict:
        meta = get(sid)
        if meta is None:
            return {"stat": "unknown index", "data": []}

        date = meta.month_param(year, month)

        ohlc = self._get(meta.ohlc_url, date, retry)
        twse_throttle.acquire()
        volume = self._get(meta.volume_url, date, retry)

        return {"stat": "OK", "data": self._merge(ohlc, volume, meta.volume_scale)}

    def _get(self, url: str, date: str, retry: int) -> list:
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
            if str(payload.get("stat") or "").lower() == "ok":
                return _table_rows(payload)
            # Months before the index existed, or a holiday-only month.
            logger.info("index report empty url=%s date=%s stat=%s", url, date, payload.get("stat"))
            return []
        return []

    @staticmethod
    def _merge(ohlc: list, volume: list, volume_scale: int = 1) -> list:
        """OHLC drives the row set; volume columns are joined on by date."""
        by_date = {}
        for row in volume:
            #  日期, 成交股數或張數, 成交金額, 成交筆數, 收盤指數, 漲跌點數
            if len(row) < 6:
                continue
            day = _report_date(row[0])
            if day is not None:
                by_date[day] = row

        merged = []
        for row in ohlc:
            #  日期, 開盤, 最高, 最低, 收盤 [, 漲跌]
            if len(row) < 5:
                continue
            day = _report_date(row[0])
            if day is None:
                continue
            vol = by_date.get(day)
            capacity = turnover = transaction = change = None
            if vol is not None:
                shares = _to_int(vol[1])
                amount = _to_int(vol[2])
                capacity = shares * volume_scale if shares is not None else None
                turnover = amount * volume_scale if amount is not None else None
                transaction = _to_int(vol[3])
                change = _to_float(vol[5])
            if change is None and len(row) > 5:
                change = _to_float(row[5])
            merged.append(
                DATATUPLE(
                    date=day,
                    capacity=capacity,
                    turnover=turnover,
                    open=_to_float(row[1]),
                    high=_to_float(row[2]),
                    low=_to_float(row[3]),
                    close=_to_float(row[4]),
                    change=change,
                    transaction=transaction,
                    note="",
                )
            )
        return merged
