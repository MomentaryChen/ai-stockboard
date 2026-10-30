"""六大類盤面因子：總體、基本面、籌碼、估值情緒、國際、突發事件。

每個因子都只用當時已公布的資料（官方統計以保守公布延遲對齊），再以秩相關與區塊自助法檢定。
未通過檢定的因子只陳述現況，不得當作訊號。
"""
import json
import os
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

H = {"User-Agent": "Mozilla/5.0"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")
EXTRA_TICKERS = {
    "^IRX": ("美3個月利率", "匯率利率"), "^VIX3M": ("VIX 3 個月", "風險"), "IEF": ("美7-10年公債", "匯率利率"),
    "HYG": ("高收益債", "風險"), "TIP": ("抗通膨債", "匯率利率"),
    "MSFT": ("Microsoft", "AI 大客戶"), "GOOGL": ("Alphabet", "AI 大客戶"), "AMZN": ("Amazon", "AI 大客戶"),
    "META": ("Meta", "AI 大客戶"), "NVDA": ("NVIDIA", "AI 大客戶"), "INTC": ("Intel", "AI 大客戶"),
    "MU": ("美光", "產業循環"), "2409.TW": ("友達", "產業循環"), "3481.TW": ("群創", "產業循環"),
    "2603.TW": ("長榮", "產業循環"),
}
HYPERSCALERS = ["MSFT", "GOOGL", "AMZN", "META", "NVDA"]
CAPEX = ["MSFT", "GOOGL", "AMZN", "META"]
EARNINGS = ["GOOGL", "MSFT", "AMZN", "META", "NVDA", "INTC", "TSM"]

# (日期, 類別, 事件)。日期為公開史實；統計的是事件後台股表現，不是因果證明。
EVENTS = [
    ("2011-03-11", "天災", "日本 311 地震"), ("2016-02-06", "天災", "台南地震"), ("2024-04-03", "天災", "花蓮地震"),
    ("2020-01-23", "疫情", "武漢封城"), ("2020-03-11", "疫情", "WHO 宣布全球大流行"),
    ("2022-02-24", "戰爭與地緣", "俄羅斯入侵烏克蘭"), ("2023-10-07", "戰爭與地緣", "以哈戰爭爆發"),
    ("2022-08-02", "兩岸", "裴洛西訪台"), ("2023-04-08", "兩岸", "聯合利劍軍演"), ("2024-05-23", "兩岸", "聯合利劍 2024A"),
    ("2018-03-22", "貿易與管制", "美國 301 關稅"), ("2019-05-05", "貿易與管制", "美中關稅升級"),
    ("2022-10-07", "貿易與管制", "美國晶片出口管制"), ("2025-04-02", "貿易與管制", "美國對等關稅"),
    ("2015-08-11", "金融", "人民幣突然貶值"), ("2023-03-10", "金融", "矽谷銀行倒閉"), ("2024-08-05", "金融", "日圓套利平倉股災"),
    ("2016-11-08", "選舉", "美國大選 2016"), ("2020-11-03", "選舉", "美國大選 2020"), ("2024-11-05", "選舉", "美國大選 2024"),
    ("2018-11-06", "選舉", "美國期中選舉 2018"), ("2022-11-08", "選舉", "美國期中選舉 2022"),
    ("2020-01-11", "選舉", "台灣大選 2020"), ("2024-01-13", "選舉", "台灣大選 2024"),
]


def finmind(dataset, start="2012-01-01", **kw):
    r = requests.get("https://api.finmindtrade.com/api/v4/data",
                     params={"dataset": dataset, "start_date": start, **kw}, headers=H, timeout=120).json()
    d = pd.DataFrame(r.get("data", []))
    if d.empty:
        raise RuntimeError(f"{dataset}: {r.get('msg')}")
    d["date"] = pd.to_datetime(d["date"])
    return d


def bls_series():
    """官方 CPI、失業率、非農就業。BLS 免金鑰額度小，所以每日只抓一次，失敗時沿用舊快取並標記。"""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "bls.json")
    ids = {"cpi": "CUUR0000SA0", "unemp": "LNS14000000", "payroll": "CES0000000001"}
    cached = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    if cached and time.time() - cached["at"] < 86400:
        return cached["data"], False
    data = {k: {} for k in ids}
    try:
        now = pd.Timestamp.now().year
        for y0 in range(2009, now + 1, 10):
            j = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", headers={**H, "Content-Type": "application/json"},
                              json={"seriesid": list(ids.values()), "startyear": str(y0), "endyear": str(min(y0 + 9, now))},
                              timeout=60).json()
            if j.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError(str(j.get("message")))
            back = {v: k for k, v in ids.items()}
            for s in j["Results"]["series"]:
                for row in s["data"]:
                    if row["period"].startswith("M") and row["period"] != "M13" and row["value"] not in ("-", ""):
                        data[back[s["seriesID"]]][f"{row['year']}-{row['period'][1:]}"] = float(row["value"])
        json.dump({"at": time.time(), "data": data}, open(path, "w", encoding="utf-8"))
        return data, False
    except Exception:
        if cached:
            return cached["data"], True
        raise


def published(monthly, idx, lag_days=20):
    """月資料 M 視為 M 月底 + lag_days 才公布，避免回測用到當時還不知道的數字。"""
    s = monthly.copy()
    s.index = s.index + pd.offsets.MonthEnd(0) + pd.Timedelta(days=lag_days)
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)


def add_factors(df, bars, asof, twc):
    idx = df.index
    missing = []
    log = lambda tk: np.log(asof[tk])
    meta = []

    def add(key, label, cat, value, star=False):
        df[key] = value
        meta.append({"key": key, "label": label, "category": cat, "star": star})

    # 一、總體經濟
    add("rate3m_chg60", "短端利率 60 日變化（央行政策）", "總體經濟", asof["^IRX"].diff(60), True)
    add("y10_chg20", "美 10 年殖利率 20 日變化", "總體經濟", asof["^TNX"].diff(20))
    add("curve", "殖利率曲線（10 年減 3 個月）", "總體經濟", asof["^TNX"] - asof["^IRX"])
    add("breakeven_chg20", "通膨預期 20 日變化（TIP/IEF）", "總體經濟", log("TIP").diff(20) - log("IEF").diff(20))
    add("twd_chg20", "美元兌台幣 20 日變化（正值為台幣貶）", "總體經濟", log("TWD=X").diff(20), True)
    try:
        bls, stale = bls_series()
        if stale:
            missing.append("BLS 更新失敗，沿用舊快取")
        m = {k: pd.Series(v).rename(index=lambda p: pd.Timestamp(p + "-01")).sort_index() for k, v in bls.items()}
        add("cpi_yoy", "美國 CPI 年增率", "總體經濟", published(m["cpi"].pct_change(12, fill_method=None), idx))
        add("cpi_accel", "CPI 年增率 3 個月變化", "總體經濟", published(m["cpi"].pct_change(12, fill_method=None).diff(3), idx))
        add("unemp_chg6", "失業率 6 個月變化", "總體經濟", published(m["unemp"].diff(6), idx))
        add("payroll_yoy", "非農就業年增率（經濟成長）", "總體經濟", published(m["payroll"].pct_change(12, fill_method=None), idx))
    except Exception as e:
        missing.append(f"BLS 通膨與就業：{e}")

    # 二、企業基本面（AI 大客戶以股價代理，資本支出與財報另列現況表）
    basket = pd.concat([log(t).diff(20) for t in HYPERSCALERS if t in asof], axis=1).mean(axis=1)
    add("hyper_rel20", "AI 大客戶相對 S&P 500（20 日）", "企業基本面", basket - log("^GSPC").diff(20), True)
    try:
        rev = finmind("TaiwanStockMonthRevenue", start="2010-01-01", data_id="2330").set_index("date")["revenue"].sort_index()
        roll = rev.rolling(3).sum()
        s = roll.pct_change(12, fill_method=None)
        s.index = s.index + pd.Timedelta(days=10)  # date 是次月 1 日，法定 10 日前公布
        add("tsm_rev_yoy", "台積電近 3 月營收年增", "企業基本面", s.reindex(s.index.union(idx)).ffill().reindex(idx), True)
    except Exception as e:
        missing.append(f"台積電營收：{e}")
    tw_log = np.log(twc)
    panel = (log("2409.TW").diff(60) + log("3481.TW").diff(60)) / 2
    add("panel_rel60", "面板相對大盤（60 日）", "企業基本面", panel - tw_log.diff(60))
    add("memory_rel60", "記憶體相對費半（60 日）", "企業基本面", log("MU").diff(60) - log("^SOX").diff(60))
    add("ship_rel60", "航運相對大盤（60 日）", "企業基本面", log("2603.TW").diff(60) - tw_log.diff(60))

    # 三、資金與籌碼
    try:
        f = finmind("TaiwanStockTotalInstitutionalInvestors")
        buy = f.pivot_table(index="date", columns="name", values="buy").reindex(idx).astype(float)
        sell = f.pivot_table(index="date", columns="name", values="sell").reindex(idx).astype(float)
        net = buy - sell
        gross = (buy + sell)
        ratio = lambda name, n: net[name].rolling(n, min_periods=int(n * .8)).sum() / gross[name].rolling(n, min_periods=int(n * .8)).sum()
        add("foreign_5d", "外資 5 日淨買賣比", "資金與籌碼", ratio("Foreign_Investor", 5), True)
        add("foreign_20d", "外資 20 日淨買賣比", "資金與籌碼", ratio("Foreign_Investor", 20), True)
        add("trust_20d", "投信 20 日淨買賣比", "資金與籌碼", ratio("Investment_Trust", 20))
        add("dealer_20d", "自營商 20 日淨買賣比", "資金與籌碼", ratio("Dealer_self", 20))
        df.attrs["foreign_month"] = float(net["Foreign_Investor"].tail(20).sum() / 1e8)
    except Exception as e:
        missing.append(f"三大法人：{e}")
    try:
        fx = finmind("TaiwanFuturesInstitutionalInvestors", start="2018-01-01", data_id="TX")
        fx = fx[fx["institutional_investors"] == "外資"].set_index("date").sort_index()
        oi = (fx["long_open_interest_balance_volume"] - fx["short_open_interest_balance_volume"]).astype(float)
        add("foreign_oi", "外資台指期淨未平倉", "資金與籌碼", oi.reindex(idx), True)
    except Exception as e:
        missing.append(f"外資期貨：{e}")
    try:
        mg = finmind("TaiwanStockTotalMarginPurchaseShortSale")
        mg = mg[mg["name"] == "MarginPurchaseMoney"].set_index("date")["TodayBalance"].astype(float).sort_index()
        mg = mg.reindex(idx).ffill(limit=2)
        add("margin_chg20", "融資餘額 20 日變化", "資金與籌碼", mg.pct_change(20, fill_method=None))
        add("margin_vs_index", "融資增幅減指數漲幅（20 日）", "資金與籌碼", mg.pct_change(20, fill_method=None) - twc.pct_change(20))
        df.attrs["margin_bn"] = float(mg.dropna().iloc[-1] / 1e8)
    except Exception as e:
        missing.append(f"融資餘額：{e}")
    pos = pd.Series(np.arange(len(idx)), index=idx)
    remain = pos.groupby(idx.to_period("Q")).transform("max") - pos
    add("qtr_end", "季底前 10 個交易日（作帳）", "資金與籌碼", (remain < 10).astype(float))
    mrem = pos.groupby(idx.to_period("M")).transform("max") - pos
    add("msci_window", "MSCI 季調月最後 5 日（2、5、8、11 月）", "資金與籌碼",
        ((mrem < 5) & idx.month.isin([2, 5, 8, 11])).astype(float))

    # 四、估值與市場情緒
    try:
        per = finmind("TaiwanStockPER", data_id="2330").set_index("date").sort_index()
        add("per_2330", "台積電本益比（大盤代理）", "估值與情緒", per["PER"].astype(float).reindex(idx).ffill(limit=5), True)
        add("pbr_2330", "台積電股價淨值比", "估值與情緒", per["PBR"].astype(float).reindex(idx).ffill(limit=5))
    except Exception as e:
        missing.append(f"本益比：{e}")
    add("heat_60", "加權 60 日漲幅（題材熱度代理）", "估值與情緒", twc.pct_change(60))
    add("heat_252", "加權一年漲幅（高檔程度）", "估值與情緒", twc.pct_change(252))
    add("vix_level", "VIX 恐慌指數", "估值與情緒", asof["^VIX"])
    add("vix_term", "VIX 期限結構（VIX／3 個月）", "估值與情緒", asof["^VIX"] / asof["^VIX3M"])
    add("credit_chg20", "信用風險偏好（HYG/IEF 20 日）", "估值與情緒", log("HYG").diff(20) - log("IEF").diff(20))
    ma60, ma120 = twc.rolling(60).mean(), twc.rolling(120).mean()
    add("vs_ma60", "距季線", "估值與情緒", twc / ma60 - 1)
    add("vs_ma120", "距半年線", "估值與情緒", twc / ma120 - 1)
    below = (twc < ma120).astype(float)
    add("broke_ma120", "近 5 日跌破半年線", "估值與情緒", ((below == 1) & (below.shift(5) == 0)).astype(float))

    # 五、國際與政治
    add("sox_20", "費半 20 日報酬", "國際與政治", log("^SOX").diff(20), True)
    add("ixic_20", "那斯達克 20 日報酬", "國際與政治", log("^IXIC").diff(20), True)
    add("tsm_20", "台積電 ADR 20 日報酬", "國際與政治", log("TSM").diff(20), True)
    add("oil_chg20", "油價 20 日變化（地緣風險代理）", "國際與政治", log("CL=F").diff(20))
    add("gold_chg20", "金價 20 日變化（避險需求）", "國際與政治", log("GC=F").diff(20))
    return meta, missing


def rank_ic(x, y, block, seed=3, n=1500):
    d = pd.DataFrame({"x": x, "y": y}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 300 or d["x"].nunique() < 2:
        return None
    rx, ry = d["x"].rank(), d["y"].rank()
    z = ((rx - rx.mean()) / rx.std() * (ry - ry.mean()) / ry.std()).values
    rng = np.random.default_rng(seed)
    count = int(np.ceil(len(z) / block))
    starts = rng.integers(0, len(z) - block + 1, size=(n, count))
    cum = np.r_[0, np.cumsum(z)]
    means = (cum[starts + block] - cum[starts]).mean(axis=1) / block
    half = len(z) // 2
    return {"ic": float(z.mean()), "lo": float(np.percentile(means, .5)), "hi": float(np.percentile(means, 99.5)),
            "ic_first": float(z[:half].mean()), "ic_second": float(z[half:].mean()), "n": int(len(d)),
            "start": d.index[0].strftime("%Y-%m-%d")}


def test_factors(df, meta):
    """99% 區間不含 0、前後半段同號、|IC|≥0.05 才算通過，用嚴格門檻壓低多重檢定的假訊號。"""
    out = []
    last = df.iloc[-1]
    for m in meta:
        s = df[m["key"]].replace([np.inf, -np.inf], np.nan)
        cur = s.dropna()
        row = {**m, "value": None if cur.empty else float(cur.iloc[-1]),
               "as_of": None if cur.empty else cur.index[-1].strftime("%Y-%m-%d")}
        if cur.empty:
            out.append({**row, "verdict": "無資料"})
            continue
        binary = cur.nunique() <= 2
        row["pct"] = float((cur < cur.iloc[-1]).mean())
        tests = {}
        for h, tgt in [(20, "fwd20"), (60, "fwd60")]:
            tests[h] = rank_ic(s, df[tgt], max(h, 20))
        row["ic20"], row["ic60"] = tests[20], tests[60]
        passed = []
        for h, t in tests.items():
            if t and t["lo"] * t["hi"] > 0 and t["ic_first"] * t["ic_second"] > 0 and abs(t["ic"]) >= .05:
                passed.append((h, t["ic"]))
        row["passed"] = [p[0] for p in passed]
        # 同狀態的歷史表現：二元因子看是否成立，連續因子看三等分
        if binary:
            same = s == cur.iloc[-1]
        else:
            q1, q2 = s.quantile(1 / 3), s.quantile(2 / 3)
            v = cur.iloc[-1]
            same = (s <= q1) if v <= q1 else (s > q2) if v > q2 else ((s > q1) & (s <= q2))
            row["zone"] = "低" if v <= q1 else "高" if v > q2 else "中"
        for tgt in ("fwd20", "fwd60"):
            r = df.loc[same, tgt].replace([np.inf, -np.inf], np.nan).dropna()
            base = df.loc[s.notna(), tgt].replace([np.inf, -np.inf], np.nan).dropna()
            row[f"{tgt}_same"] = float(r.mean()) if len(r) else None
            row[f"{tgt}_base"] = float(base.mean()) if len(base) else None
            row[f"{tgt}_win"] = float((r > 0).mean()) if len(r) else None
        if not passed:
            row["verdict"] = "未通過"
            row["signal"] = "只陳述"
        else:
            ic = passed[-1][1]
            tilt = 0
            if binary:
                tilt = 1 if cur.iloc[-1] > 0 else 0
            elif row.get("zone") == "高":
                tilt = 1
            elif row.get("zone") == "低":
                tilt = -1
            score = np.sign(ic) * tilt
            row["verdict"] = "通過"
            row["signal"] = "偏多" if score > 0 else "偏空" if score < 0 else "中性"
        out.append(row)
    return out


def event_study(twc, today_ret=None):
    rows = []
    idx = twc.index
    for date, cat, name in EVENTS:
        d = pd.Timestamp(date)
        i = idx.searchsorted(d, side="left")
        if i <= 0 or i >= len(idx):
            continue
        p = twc.values
        row = {"date": date, "category": cat, "event": name, "first": idx[i].strftime("%Y-%m-%d"),
               "d0": float(p[i] / p[i - 1] - 1)}
        for h in (5, 20, 60):
            row[f"d{h}"] = float(p[i + h] / p[i] - 1) if i + h < len(p) else None
        window = p[i:i + 61]
        row["max_dd"] = float(window.min() / p[i - 1] - 1)
        rows.append(row)
    return rows


def fundamentals():
    capex, earn, problems = [], [], []
    for t in CAPEX:
        try:
            s = yf.Ticker(t).quarterly_cashflow.loc["Capital Expenditure"].dropna().sort_index()
            for d, v in s.tail(5).items():
                capex.append({"ticker": t, "quarter": d.strftime("%Y-%m-%d"), "capex_bn": abs(float(v)) / 1e9})
        except Exception as e:
            problems.append(f"{t} 資本支出：{str(e)[:80]}")
    for t in EARNINGS:
        try:
            e = yf.Ticker(t).earnings_dates.dropna(subset=["Reported EPS"])
            ts = e.index[0]
            px = yf.Ticker(t).history(start=(ts - pd.Timedelta(days=10)).strftime("%Y-%m-%d"), auto_adjust=False)["Close"]
            px.index = px.index.tz_localize(None).normalize()
            day = pd.Timestamp(ts.date())
            # 收盤後公布看下一交易日，開盤前公布看當日
            i = px.index.searchsorted(day, side="left") + (1 if ts.hour >= 16 else 0)
            react = float(px.iloc[i] / px.iloc[i - 1] - 1) if 0 < i < len(px) else None
            earn.append({"ticker": t, "date": str(day.date()), "eps_est": float(e.iloc[0]["EPS Estimate"]),
                         "eps": float(e.iloc[0]["Reported EPS"]), "surprise": float(e.iloc[0]["Surprise(%)"]) / 100,
                         "reaction": react})
        except Exception as ex:
            problems.append(f"{t} 財報：{str(ex)[:80]}")
    return capex, earn, problems
