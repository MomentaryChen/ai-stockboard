"""Who gets scored: the largest companies in each sector, ranked fresh every run."""

from __future__ import annotations

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
TOP_N = 5

# (sector, TWSE industry code, the same industry's name in FinMind's TPEx listing)
SECTORS = [
    ("半導體", "24", "半導體業"),
    ("記憶體", None, None),
    ("電腦及週邊", "25", "電腦及週邊設備業"),
    ("電子零組件", "28", "電子零組件業"),
    ("其他電子（含代工）", "31", "其他電子業"),
    ("通信網路", "27", "通信網路業"),
    ("電子通路", "29", "電子通路業"),
    ("光電", "26", "光電業"),
    ("建材營造（房市）", "14", "建材營造"),
    ("水泥", "01", "水泥工業"),
    ("金融保險", "17", "金融保險"),
    ("航運", "15", "航運業"),
    ("鋼鐵", "10", "鋼鐵工業"),
]

# The exchanges have no memory category, so this list is a judgement call. Its
# members are removed from 半導體 so one company is never scored twice.
MEMORY = {"2408", "2344", "2337", "8299", "3260", "4967", "3006", "2451"}


def _get(url: str, **params):
    response = requests.get(url, params=params or None, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_listing() -> pd.DataFrame:
    listed = pd.DataFrame(_get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"))
    quotes = pd.DataFrame(_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"))
    quotes["close"] = pd.to_numeric(quotes["ClosingPrice"], errors="coerce")
    merged = listed.merge(quotes[["Code", "close", "Date"]], left_on="公司代號", right_on="Code")
    merged["shares"] = pd.to_numeric(merged["已發行普通股數或TDR原股發行股數"], errors="coerce")
    rows = [
        {
            "code": r["公司代號"],
            "name": r["公司簡稱"].strip(),
            "industry": r["產業別"],
            "cap": r["close"] * r["shares"],
            "official_close": r["close"],
            "official_date": r["Date"],
            "yahoo": r["公司代號"] + ".TW",
            "market": "上市",
        }
        for _, r in merged.iterrows()
        if len(r["公司代號"]) == 4 and pd.notna(r["close"]) and pd.notna(r["shares"])
    ]

    otc = pd.DataFrame(_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"))
    otc["close"] = pd.to_numeric(otc["Close"], errors="coerce")
    otc["shares"] = pd.to_numeric(otc["Capitals"], errors="coerce")
    info = pd.DataFrame(_get("https://api.finmindtrade.com/api/v4/data", dataset="TaiwanStockInfo")["data"])
    info = info[info["type"] == "tpex"]
    wanted = {name for _, _, name in SECTORS if name}
    industry = {
        row.stock_id: row.industry_category
        for row in info.itertuples()
        if row.industry_category in wanted
    }
    for _, r in otc.iterrows():
        code = r["SecuritiesCompanyCode"]
        if len(code) == 4 and pd.notna(r["close"]) and pd.notna(r["shares"]) and (code in industry or code in MEMORY):
            rows.append(
                {
                    "code": code,
                    "name": r["CompanyName"].strip(),
                    "industry": industry.get(code),
                    "cap": r["close"] * r["shares"],
                    "official_close": r["close"],
                    "official_date": r["Date"],
                    "yahoo": code + ".TWO",
                    "market": "上櫃",
                }
            )
    return pd.DataFrame(rows)


def pick(listing: pd.DataFrame) -> list[dict]:
    picks = []
    for sector, code, otc_name in SECTORS:
        if sector == "記憶體":
            group = listing[listing["code"].isin(MEMORY)]
        else:
            group = listing[
                ((listing["market"] == "上市") & (listing["industry"] == code))
                | ((listing["market"] == "上櫃") & (listing["industry"] == otc_name))
            ]
            group = group[~group["code"].isin(MEMORY)]
        top = group.sort_values("cap", ascending=False).head(TOP_N)
        for rank, (_, row) in enumerate(top.iterrows(), 1):
            picks.append({"sector": sector, "rank": rank, "members": int(len(group)), **row.to_dict()})
    return picks


def twse_stock_day(stock: str, month: str) -> dict[pd.Timestamp, list[float]]:
    """Official daily bars for one month, used to fill holes in Yahoo's history."""
    payload = _get(
        "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
        response="json",
        date=month + "01",
        stockNo=stock,
    )
    bars = {}
    for row in payload.get("data") or []:
        y, m, d = row[0].split("/")
        try:
            bars[pd.Timestamp(int(y) + 1911, int(m), int(d))] = [
                float(row[i].replace(",", "")) for i in (3, 4, 5, 6)
            ]
        except ValueError:
            # A halted session prints "--" instead of prices.
            continue
    return bars


def official_index_closes() -> dict[pd.Timestamp, float]:
    closes = {}
    for row in _get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"):
        date = row["Date"]
        closes[pd.Timestamp(int(date[:3]) + 1911, int(date[3:5]), int(date[5:7]))] = float(
            row["TAIEX"].replace(",", "")
        )
    return closes


# No exchange publishes "AI companies", so membership is a judgement call and the
# page says so. Only the ranking inside each group is computed.
US_GROUPS = {
    "AI 晶片": ["NVDA", "AVGO", "TSM", "AMD", "MU", "INTC", "QCOM", "MRVL", "ARM"],
    "半導體設備": ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
    "雲端與模型平台": ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
    "AI 伺服器與基建": ["DELL", "ANET", "SMCI", "VRT", "CSCO", "HPE"],
    "AI 軟體與應用": ["PLTR", "NOW", "CRM", "ADBE", "SNOW"],
}


def us_picks() -> list[dict]:
    import yfinance as yf  # Only this function needs it, and tests of `pick` should not.

    picks = []
    for group, tickers in US_GROUPS.items():
        members = []
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).info
            except Exception:  # One failed lookup only costs that company its ranking.
                continue
            if info.get("marketCap"):
                members.append((ticker, info))
        members.sort(key=lambda m: m[1]["marketCap"], reverse=True)
        for rank, (ticker, info) in enumerate(members[:TOP_N], 1):
            picks.append(
                {
                    "sector": group,
                    "rank": rank,
                    "members": len(members),
                    "code": ticker,
                    "name": info.get("shortName") or ticker,
                    "cap": float(info["marketCap"]),
                    "official_close": None,
                    "official_date": None,
                    "yahoo": ticker,
                    "market": "美股",
                    # Earnings often move a stock after the close, which the
                    # close-based figures cannot see, so the reader needs this.
                    "extended_price": info.get("postMarketPrice") or info.get("preMarketPrice"),
                    "extended_change": info.get("postMarketChangePercent")
                    if info.get("postMarketPrice")
                    else info.get("preMarketChangePercent"),
                }
            )
    return picks
