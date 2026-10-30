#!/usr/bin/env python
"""台股盤面掃描：全球趨勢、領先落後、樣本外驗證、類似盤面期望值、進場價位與部位。

所有數字都來自歷史行情的條件分佈，不使用主觀預測。
時間對齊規則：第 i 列 = 台股第 i 日收盤後、第 i+1 日開盤前可得的全部資訊。
"""
import argparse
import json
import sys
import warnings
from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import factors as fx

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

# ticker: (名稱, 群組, 時區, 收盤確認時間)
NY, TPE = "America/New_York", "Asia/Taipei"
UNIVERSE = {
    "^TWII": ("台股加權", "台灣", TPE, time(14, 0)),
    "0050.TW": ("元大台灣50", "台灣", TPE, time(14, 0)),
    "2330.TW": ("台積電", "台灣", TPE, time(14, 0)),
    "^GSPC": ("S&P 500", "美股", NY, time(16, 30)),
    "^IXIC": ("Nasdaq", "美股", NY, time(16, 30)),
    "^DJI": ("道瓊", "美股", NY, time(16, 30)),
    "^SOX": ("費城半導體", "美股", NY, time(16, 30)),
    "TSM": ("台積電 ADR", "美股", NY, time(16, 30)),
    "^VIX": ("VIX", "風險", NY, time(16, 30)),
    "^N225": ("日經 225", "亞股", "Asia/Tokyo", time(15, 45)),
    "^KS11": ("韓國 KOSPI", "亞股", "Asia/Seoul", time(15, 45)),
    "^HSI": ("香港恆生", "亞股", "Asia/Hong_Kong", time(16, 20)),
    "000001.SS": ("上證綜指", "亞股", "Asia/Shanghai", time(15, 15)),
    "^STI": ("新加坡 STI", "亞股", "Asia/Singapore", time(17, 15)),
    "^AXJO": ("澳洲 ASX200", "亞股", "Australia/Sydney", time(16, 30)),
    "^GDAXI": ("德國 DAX", "歐股", "Europe/Berlin", time(17, 45)),
    "^FTSE": ("英國 FTSE", "歐股", "Europe/London", time(16, 45)),
    "TWD=X": ("美元/台幣", "匯率利率", NY, time(17, 0)),
    "DX-Y.NYB": ("美元指數", "匯率利率", NY, time(17, 0)),
    "^TNX": ("美10年殖利率", "匯率利率", NY, time(17, 0)),
    "CL=F": ("WTI 原油", "商品", NY, time(17, 0)),
    "GC=F": ("黃金", "商品", NY, time(17, 0)),
    "ES=F": ("S&P 期貨", "期貨", NY, time(17, 0)),
    "NQ=F": ("Nasdaq 期貨", "期貨", NY, time(17, 0)),
}
for _tk, (_name, _group) in fx.EXTRA_TICKERS.items():
    UNIVERSE[_tk] = (_name, _group, TPE if _tk.endswith(".TW") else NY, time(14, 0) if _tk.endswith(".TW") else time(17, 0))
BREADTH = ["^TWII", "^GSPC", "^IXIC", "^DJI", "^SOX", "^N225", "^KS11", "^HSI",
           "000001.SS", "^STI", "^AXJO", "^GDAXI", "^FTSE"]
LEAD_LAG = ["^SOX", "^IXIC", "^GSPC", "TSM", "^VIX", "^GDAXI", "^N225", "^KS11", "^HSI", "000001.SS"]
FEATURES = ["sox", "ixic", "gspc", "tsm", "vix", "dxy", "tnx", "twd", "tw_ret1", "tw_ret5", "tw_ret20",
            "dist_ma20", "dist_ma60", "dist_ma200", "dd252", "rv20", "vix_lvl", "rsi14", "breadth"]
KNN_FEATURES = ["sox", "tsm", "vix", "tw_ret1", "tw_ret5", "tw_ret20", "dist_ma60", "dist_ma200", "dd252",
                "rv20", "vix_lvl", "breadth"]
HORIZONS = [1, 5, 20, 60]
ROUND_TRIP_COST = 0.00385  # 手續費 0.1425% x 2 + ETF 證交稅 0.1%


def download(start):
    raw = yf.download(list(UNIVERSE), start=start, auto_adjust=False, group_by="ticker",
                      threads=True, progress=False)
    now = datetime.now(ZoneInfo("UTC"))
    bars, live, dropped = {}, {}, []
    for tk, (_, _, tz, close_at) in UNIVERSE.items():
        if tk not in raw.columns.get_level_values(0):
            dropped.append(tk)
            continue
        df = raw[tk].dropna(subset=["Close"]).copy()
        if df.empty:
            dropped.append(tk)
            continue
        live[tk] = {"date": df.index[-1].strftime("%Y-%m-%d"), "close": float(df["Close"].iloc[-1]),
                    "prev": float(df["Close"].iloc[-2])}
        local = now.astimezone(ZoneInfo(tz))
        # 未收盤的當日 K 棒不可進模型，否則等於用盤中資料回測
        if df.index[-1].date() >= local.date() and local.time() < close_at:
            df = df.iloc[:-1]
            live[tk]["partial"] = True
        bars[tk] = df
    return bars, live, dropped


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def twse_stock_day(stock, month):
    j = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                     params={"response": "json", "date": month + "01", "stockNo": stock},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
    rows = {}
    for r in j.get("data") or []:
        y, m, d = r[0].split("/")
        rows[pd.Timestamp(int(y) + 1911, int(m), int(d))] = [float(r[i].replace(",", "")) for i in (3, 4, 5, 6)]
    return rows


def backfill_0050(etf, calendar):
    """用證交所官方日線補 Yahoo 缺的交易日；僅補近一年，更早的缺口由交集排除。"""
    etf = etf.loc[etf.index.intersection(calendar)].copy()
    missing = calendar.difference(etf.index)
    missing = missing[missing >= calendar[-1] - pd.Timedelta(days=365)]
    for month in sorted({d.strftime("%Y%m") for d in missing}):
        official = twse_stock_day("0050", month)
        for d in missing:
            if d.strftime("%Y%m") == month and d in official:
                o, h, l, c = official[d]
                etf.loc[d, ["Open", "High", "Low", "Close", "Adj Close"]] = [o, h, l, c, c]
    return etf.sort_index()


def check_quality(bars, twc):
    """跟證交所官方收盤核對；對不上就中止，不輸出可能錯誤的結論。"""
    rows = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
    checked = 0
    for r in rows:
        y, m, d = int(r["Date"][:3]) + 1911, int(r["Date"][3:5]), int(r["Date"][5:7])
        ts = pd.Timestamp(y, m, d)
        official = float(r["TAIEX"].replace(",", ""))
        if ts not in twc.index:
            sys.exit(f"資料品質錯誤：官方交易日 {ts.date()} 不在面板內")
        if abs(twc.loc[ts] / official - 1) > 0.001:
            sys.exit(f"資料品質錯誤：{ts.date()} Yahoo {twc.loc[ts]:.2f} != 官方 {official:.2f}")
        checked += 1
    return {"twse_days_checked": checked, "last_official": rows[-1]["Date"]}


def build_panel(bars):
    tw = bars["^TWII"]
    # 交易日以加權指數為準：Yahoo 的 0050 偶有缺日，也會在休市日產生假 K 棒
    idx = tw.index
    etf = backfill_0050(bars["0050.TW"], idx)
    # Yahoo 早年部分 K 棒的 OHLC 為 0，以前一日收盤修復，避免算出無限大報酬
    prev = etf["Close"].shift()
    for col in ("Open", "High", "Low"):
        etf[col] = etf[col].where(etf[col] > 0, prev)
    etf = etf.dropna(subset=["Open"])
    bars["0050.TW"] = etf
    idx = idx.intersection(etf.index)
    f = etf["Adj Close"] / etf["Close"]
    p = pd.DataFrame({"o": etf["Open"] * f, "h": etf["High"] * f, "l": etf["Low"] * f, "c": etf["Adj Close"]}).loc[idx]
    twc = tw["Close"].loc[idx]
    out = pd.DataFrame(index=idx)
    # 外部市場取「下一個台股開盤前」最後一筆已收盤資料，並計算兩列之間的累積變化
    nxt = pd.Series(list(idx[1:]) + [pd.Timestamp("2100-01-01")], index=idx)
    asof = {}
    for tk, df in bars.items():
        s = df["Close"]
        pos = s.index.searchsorted(nxt.values, side="left") - 1
        vals = np.where(pos >= 0, s.values[np.clip(pos, 0, len(s) - 1)], np.nan)
        asof[tk] = pd.Series(vals, index=idx)
    for name, tk in [("sox", "^SOX"), ("ixic", "^IXIC"), ("gspc", "^GSPC"), ("tsm", "TSM"), ("vix", "^VIX"),
                     ("dxy", "DX-Y.NYB"), ("twd", "TWD=X")]:
        out[name] = np.log(asof[tk]).diff()
    out["tnx"] = asof["^TNX"].diff()
    out["vix_lvl"] = asof["^VIX"]
    out["tw_ret1"] = twc.pct_change()
    out["tw_ret5"] = twc.pct_change(5)
    out["tw_ret20"] = twc.pct_change(20)
    for n in (20, 60, 200):
        out[f"dist_ma{n}"] = twc / twc.rolling(n).mean() - 1
    out["dd252"] = twc / twc.rolling(252, min_periods=120).max() - 1
    out["rv20"] = np.log(twc).diff().rolling(20).std() * np.sqrt(252)
    out["rsi14"] = rsi(twc)
    above = []
    for tk in BREADTH:
        if tk in asof:
            above.append((asof[tk] > asof[tk].rolling(50).mean()).astype(float))
    out["breadth"] = pd.concat(above, axis=1).mean(axis=1)
    tr = pd.concat([p["h"] - p["l"], (p["h"] - p["c"].shift()).abs(), (p["l"] - p["c"].shift()).abs()], axis=1).max(axis=1)
    out["atr_pct"] = tr.rolling(14).mean() / p["c"]
    out["tw_next"] = twc.shift(-1) / twc - 1
    # 目標值：從下一日開盤進場
    next_open = p["o"].shift(-1)
    out["gap"] = next_open / p["c"] - 1
    out["cc1"] = p["c"].shift(-1) / p["c"] - 1
    out["intraday"] = p["c"].shift(-1) / next_open - 1
    for h in HORIZONS:
        out[f"fwd{h}"] = p["c"].shift(-h) / next_open - 1
    return out, p, twc, asof


def ridge_walk_forward(df, target, h, start="2016-01-01", lam=100.0, step=20, features=FEATURES):
    d = df.replace([np.inf, -np.inf], np.nan).dropna(subset=list(features))
    x = d[list(features)].values
    y = d[target].values
    pred = np.full(len(d), np.nan)
    begin = d.index.searchsorted(pd.Timestamp(start))
    beta, mu, sd, ym = None, None, None, None
    for i in range(begin, len(d)):
        if beta is None or (i - begin) % step == 0:
            end = i - h  # 訓練資料的目標值必須在 i 之前已實現
            mask = ~np.isnan(y[:end])
            xt, yt = x[:end][mask], y[:end][mask]
            mu, sd, ym = xt.mean(0), xt.std(0) + 1e-9, yt.mean()
            z = (xt - mu) / sd
            beta = np.linalg.solve(z.T @ z + lam * np.eye(z.shape[1]), z.T @ (yt - ym))
        pred[i] = ym + ((x[i] - mu) / sd) @ beta
    return pd.Series(pred, index=d.index), dict(zip(features, beta.tolist())), mu, sd, ym


def knn_walk_forward(df, target, h, k=120, start="2016-01-01"):
    d = df.dropna(subset=KNN_FEATURES)
    x = d[KNN_FEATURES].values
    y = d[target].values
    pred = np.full(len(d), np.nan)
    begin = d.index.searchsorted(pd.Timestamp(start))
    for i in range(begin, len(d)):
        end = i - h
        mask = ~np.isnan(y[:end])
        xt, yt = x[:end][mask], y[:end][mask]
        mu, sd = xt.mean(0), xt.std(0) + 1e-9
        dist = (((xt - mu) / sd - (x[i] - mu) / sd) ** 2).sum(1)
        pred[i] = yt[np.argpartition(dist, k)[:k]].mean()
    return pd.Series(pred, index=d.index)


def block_ci(values, block, n=2000, seed=7):
    v = np.asarray(values, float)
    v = v[~np.isnan(v)]
    if len(v) < 10:
        return [None, None]
    rng = np.random.default_rng(seed)
    block = max(1, min(block, len(v) // 2))
    count = int(np.ceil(len(v) / block))
    starts = rng.integers(0, len(v) - block + 1, size=(n, count))
    means = np.array([np.concatenate([v[s:s + block] for s in row])[:len(v)].mean() for row in starts])
    return [float(np.percentile(means, 5)), float(np.percentile(means, 95))]


def validate(df, target, h, pred):
    """樣本外驗證：跟同期可得的歷史平均比，模型到底有沒有多預測到什麼。"""
    d = pd.DataFrame({"y": df[target], "p": pred}).dropna()
    bench = df[target].shift(h).expanding(min_periods=250).mean().reindex(d.index)
    sse_model = ((d["y"] - d["p"]) ** 2).sum()
    sse_bench = ((d["y"] - bench) ** 2).sum()
    spread = (d["p"] > bench).astype(float)
    hi = d["y"][spread == 1]
    lo = d["y"][spread == 0]
    ic_series = (d["p"] - d["p"].mean()) * (d["y"] - d["y"].mean())
    ci = block_ci(ic_series.values / (d["p"].std() * d["y"].std()), max(h, 5))
    return {
        "n": int(len(d)), "effective_n": int(len(d) / max(h, 1)),
        "r2_oos": float(1 - sse_model / sse_bench),
        "ic": float(d["p"].corr(d["y"])), "ic_ci90": ci,
        "ic_rank": float(d["p"].rank().corr(d["y"].rank())),
        "hit": float(((d["p"] > 0) == (d["y"] > 0)).mean()),
        "base_up": float((d["y"] > 0).mean()),
        "mean_when_above_bench": float(hi.mean()) if len(hi) else None,
        "mean_when_below_bench": float(lo.mean()) if len(lo) else None,
    }


def dist_stats(r):
    r = pd.Series(r).dropna()
    if r.empty:
        return None
    q05 = r.quantile(.05)
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(r.median()), "win": float((r > 0).mean()),
            "p05": float(q05), "p25": float(r.quantile(.25)), "p75": float(r.quantile(.75)),
            "p95": float(r.quantile(.95)), "cvar05": float(r[r <= q05].mean())}


def neighbors(df, k, skip=()):
    feats = [f for f in KNN_FEATURES if f not in skip]
    d = df.dropna(subset=KNN_FEATURES)
    last = d.iloc[-1]
    hist = d.iloc[:-1]
    mu, sd = hist[feats].mean(), hist[feats].std() + 1e-9
    dist = (((hist[feats] - last[feats]) / sd) ** 2).sum(axis=1) ** .5
    return dist.nsmallest(k)


def episodes(dates, gap=20):
    """把連續日期併成獨立事件，避免同一段行情被重複計算。"""
    ds = pd.DatetimeIndex(sorted(dates))
    if len(ds) == 0:
        return np.array([], dtype=int)
    return np.cumsum(np.r_[0, (np.diff(ds.values) > np.timedelta64(gap, "D")).astype(int)])


def cluster_ci(values, clusters, n=2000, seed=11):
    v = pd.Series(values).reset_index(drop=True)
    c = pd.Series(clusters).reset_index(drop=True)
    groups = [g.dropna().values for _, g in v.groupby(c)]
    groups = [g for g in groups if len(g)]
    if len(groups) < 5:
        return [None, None]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        means.append(np.concatenate([groups[j] for j in pick]).mean())
    return [float(np.percentile(means, 5)), float(np.percentile(means, 95))]


def sim_trade(p, i, atr, k, stop_atr, hold, window):
    """從第 i 列收盤掛限價單，回傳 (是否成交, 扣成本報酬, 是否停損)。stop_atr=None 表示不設停損。

    保守假設：成交當日若同時碰到停損，視為當日停損；跳空開盤以開盤價成交/出場。
    """
    n = len(p)
    o, l, c = p["o"].values, p["l"].values, p["c"].values
    limit = c[i] * (1 - k * atr)
    if i + window >= n:
        return None, None, False
    fill, price = None, None
    for j in range(i + 1, i + 1 + window):
        if k == 0:
            fill, price = j, o[j]
            break
        if o[j] <= limit:
            fill, price = j, o[j]
            break
        if l[j] <= limit:
            fill, price = j, limit
            break
    if fill is None:
        return False, None, False
    if fill + hold >= n:
        return None, None, False
    if stop_atr is not None:
        stop = price * (1 - stop_atr * atr)
        for j in range(fill, fill + hold + 1):
            if j > fill and o[j] <= stop:
                return True, o[j] / price - 1 - ROUND_TRIP_COST, True
            if l[j] <= stop:
                return True, stop / price - 1 - ROUND_TRIP_COST, True
    return True, c[fill + hold] / price - 1 - ROUND_TRIP_COST, False


def entry_grid(df, p, sample, stop_atr, hold, window, levels):
    pos = {d: i for i, d in enumerate(p.index)}
    atr = df["atr_pct"]
    all_dates = df.dropna(subset=["atr_pct", "dist_ma200"]).index[:-1]
    rows = []
    for k in levels:
        rec = []
        for d in sample:
            filled, r, stopped = sim_trade(p, pos[d], atr.loc[d], k, stop_atr, hold, window)
            if filled is not None:
                rec.append((d, filled, r, stopped))
        base = []
        for d in all_dates[::3]:  # 全樣本基準（每 3 日取樣以節省時間）
            filled, r, _ = sim_trade(p, pos[d], atr.loc[d], k, stop_atr, hold, window)
            if filled is not None:
                base.append((filled, r))
        rd = pd.DataFrame(rec, columns=["d", "filled", "r", "stopped"])
        bd = pd.DataFrame(base, columns=["filled", "r"])
        got = rd[rd["filled"]]
        fill_rate = float(rd["filled"].mean()) if len(rd) else 0.0
        ev_fill = float(got["r"].mean()) if len(got) else None
        attempt = rd["r"].fillna(0.0)
        clusters = episodes(rd["d"])
        order = rd["d"].argsort().values
        ci = cluster_ci(attempt.values[order], clusters) if len(rd) else [None, None]
        ci_fill = cluster_ci(got.sort_values("d")["r"].values, episodes(got["d"])) if len(got) else [None, None]
        base_attempt = float(bd["r"].fillna(0).mean()) if len(bd) else None
        var = float(got["r"].var()) if len(got) > 2 else None
        rows.append({
            "k_atr": k, "samples": int(len(rd)), "independent_episodes": int(clusters.max() + 1) if len(rd) else 0,
            "fill_rate": fill_rate, "ev_if_filled": ev_fill, "ev_if_filled_ci90": ci_fill,
            "ev_per_attempt": float(attempt.mean()) if len(rd) else None, "ev_per_attempt_ci90": ci,
            "win_if_filled": float((got["r"] > 0).mean()) if len(got) else None,
            "p05_if_filled": float(got["r"].quantile(.05)) if len(got) else None,
            "stop_rate": float(got["stopped"].astype(float).mean()) if len(got) else None,
            "cvar05_if_filled": float(got["r"][got["r"] <= got["r"].quantile(.05)].mean()) if len(got) else None,
            "baseline_fill_rate": float(bd["filled"].mean()) if len(bd) else None,
            "baseline_ev_if_filled": float(bd["r"].mean()) if len(bd) else None,
            "baseline_ev_per_attempt": base_attempt,
            "kelly_full": float(ev_fill / var) if var and ev_fill is not None else None,
        })
    return rows


def size_position(grid, atr_now, stop_atr, risk_budget, kelly_frac, max_total):
    """每一檔各自計算部位：取 Kelly 分數與單筆風險預算的較小值，再依統計信心打折。"""
    out = []
    for g in grid:
        ev, ci = g["ev_per_attempt"], g["ev_per_attempt_ci90"]
        evf = g["ev_if_filled"]
        # 無停損時，以歷史最差 5% 情境的平均虧損當作單筆可能損失
        stop_dist = stop_atr * atr_now if stop_atr is not None else abs(g["cvar05_if_filled"] or 1.0)
        by_risk = risk_budget / stop_dist
        by_kelly = max(0.0, (g["kelly_full"] or 0) * kelly_frac)
        raw = min(by_risk, by_kelly, max_total)
        lower = ci[0] if ci else None
        excess = None if ev is None or g["baseline_ev_per_attempt"] is None else ev - g["baseline_ev_per_attempt"]
        if evf is None or evf <= 0 or lower is None or lower <= 0:
            tier, mult = "C", 0.0   # 信賴區間下緣未高於 0
        elif g["baseline_ev_per_attempt"] is not None and lower > g["baseline_ev_per_attempt"]:
            tier, mult = "A", 1.0   # 信賴區間下緣高於全樣本基準：盤面確實帶來額外優勢
        else:
            tier, mult = "B", 0.5   # EV 為正，但與長期平均無顯著差異，只是吃大盤漂移
        out.append({**g, "by_risk": by_risk, "by_kelly": by_kelly, "tier": tier, "size": raw * mult, "excess": excess})
    return out


def trend_table(bars, live):
    rows = []
    for tk, (name, group, _, _) in UNIVERSE.items():
        if tk not in bars:
            continue
        c = bars[tk]["Close"].dropna()
        if len(c) < 210:
            continue
        last = c.iloc[-1]
        ma20, ma60, ma200 = c.rolling(20).mean().iloc[-1], c.rolling(60).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
        slope = ma60 / c.rolling(60).mean().iloc[-11] - 1
        hi = c.tail(252).max()
        # 趨勢標籤只看價格與均線關係
        if last > ma20 > ma60 > ma200:
            label = "多頭排列"
        elif last < ma20 < ma60 < ma200:
            label = "空頭排列"
        elif last > ma200 and last < ma20:
            label = "多頭回檔"
        elif last < ma200 and last > ma20:
            label = "空頭反彈"
        else:
            label = "盤整"
        rows.append({"ticker": tk, "name": name, "group": group, "date": c.index[-1].strftime("%Y-%m-%d"),
                     "last": float(last), "d1": float(last / c.iloc[-2] - 1), "d5": float(last / c.iloc[-6] - 1),
                     "d20": float(last / c.iloc[-21] - 1), "d60": float(last / c.iloc[-61] - 1),
                     "vs_ma20": float(last / ma20 - 1), "vs_ma60": float(last / ma60 - 1),
                     "vs_ma200": float(last / ma200 - 1), "ma60_slope10d": float(slope),
                     "from_high": float(last / hi - 1), "trend": label,
                     "live_partial": bool(live.get(tk, {}).get("partial")),
                     "live_date": live.get(tk, {}).get("date"), "live_close": live.get(tk, {}).get("close")})
    return rows


def lead_lag(bars, twc):
    tw = np.log(twc).diff().dropna()
    rows = []
    for tk in LEAD_LAG:
        if tk not in bars:
            continue
        r = np.log(bars[tk]["Close"]).diff().dropna()
        pos = r.index.searchsorted(tw.index, side="left") - 1  # 嚴格早於台股當日
        lagged = pd.Series(np.where(pos >= 0, r.values[np.clip(pos, 0, len(r) - 1)], np.nan), index=tw.index)
        same = r.reindex(tw.index)
        row = {"ticker": tk, "name": UNIVERSE[tk][0]}
        for label, n in [("60d", 60), ("250d", 250), ("3y", 750)]:
            t, x, s = tw.tail(n), lagged.tail(n), same.tail(n)
            row[f"corr_prev_{label}"] = float(t.corr(x))
            row[f"corr_same_{label}"] = float(t.corr(s))
            row[f"beta_prev_{label}"] = float(t.cov(x) / x.var())
        rows.append(row)
    return rows


def condition_study(df, p, expr):
    hist = df.iloc[:-1]
    hit = hist.query(expr).index
    res = {"expr": expr, "dates": int(len(hit))}
    if len(hit) == 0:
        return res
    ep = episodes(hit)
    res["episodes"] = int(ep.max() + 1)
    res["last_dates"] = [d.strftime("%Y-%m-%d") for d in hit[-8:]]
    for h in HORIZONS:
        r = hist.loc[hit, f"fwd{h}"]
        res[f"fwd{h}"] = dist_stats(r)
        res[f"fwd{h}_ci90"] = cluster_ci(r.values, ep)
        res[f"base{h}"] = dist_stats(hist[f"fwd{h}"])
    return res


def twse_flows(days=8):
    rows = []
    d = pd.Timestamp.now(tz=TPE).normalize()
    tries = 0
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    while len(rows) < days and tries < days + 8:
        tries += 1
        ds = d.strftime("%Y%m%d")
        d -= pd.Timedelta(days=1)
        try:
            j = session.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
                            params={"response": "json", "dayDate": ds, "type": "day"}, timeout=15).json()
        except Exception:
            continue
        if j.get("stat") != "OK" or not j.get("data"):
            continue
        item = {"date": ds}
        for row in j["data"]:
            item[row[0]] = float(row[3].replace(",", "")) / 1e8
        rows.append(item)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--k", type=int, default=250, help="類似盤面取樣數")
    ap.add_argument("--stop-atr", type=float, default=2.5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--window", type=int, default=5, help="限價單有效交易日")
    ap.add_argument("--risk", type=float, default=0.01, help="單檔停損佔總資金比例")
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--max-total", type=float, default=0.6)
    ap.add_argument("--condition", action="append", default=[], help="pandas query，例如 'tw_ret1<-0.02 and dist_ma200>0'")
    args = ap.parse_args()

    bars, live, dropped = download(args.start)
    for must in ("^TWII", "0050.TW", "^SOX", "^VIX"):
        if must not in bars:
            sys.exit(f"缺少必要資料 {must}，無法分析")
    df, p, twc, asof = build_panel(bars)
    quality = check_quality(bars, twc)
    factor_meta, factor_missing = fx.add_factors(df, bars, asof, twc)
    factor_table = fx.test_factors(df, factor_meta)
    events = fx.event_study(twc)
    capex, earnings, fund_problems = fx.fundamentals()
    # 增量檢驗：把全部因子加進模型（不先挑選，避免選擇偏誤），看樣本外是否真的變好
    usable = [m["key"] for m in factor_meta if df[m["key"]].replace([np.inf, -np.inf], np.nan).notna().sum() > 1000
              and m["key"] != "foreign_oi"]
    with_oi = usable + (["foreign_oi"] if "foreign_oi" in df else [])
    incremental = []
    for target, h in [("intraday", 1), ("fwd5", 5), ("fwd20", 20), ("fwd60", 60)]:
        base_pred, *_ = ridge_walk_forward(df, target, h, start="2021-01-01")
        ext_pred, *_ = ridge_walk_forward(df, target, h, start="2021-01-01", features=FEATURES + with_oi)
        common = base_pred.dropna().index.intersection(ext_pred.dropna().index)
        incremental.append({"target": target, "n": int(len(common)), "base": validate(df, target, h, base_pred.loc[common]),
                            "extended": validate(df, target, h, ext_pred.loc[common])})

    validation = {}
    last_row = df.iloc[-1]
    model_now = {}
    for target, h in [("gap", 1), ("intraday", 1), ("cc1", 1), ("fwd5", 5), ("fwd20", 20), ("fwd60", 60)]:
        pred, beta, mu, sd, ym = ridge_walk_forward(df, target, h)
        pred_knn = knn_walk_forward(df, target, h)
        now = float(ym + ((df[FEATURES].dropna().iloc[-1].values - mu) / sd) @ np.array([beta[f] for f in FEATURES]))
        validation[target] = {"ridge": validate(df, target, h, pred), "knn": validate(df, target, h, pred_knn),
                              "ridge_now": now, "knn_now": float(pred_knn.iloc[-1]), "unconditional": float(df[target].mean())}
        model_now[target] = beta

    # 情境敏感度：只用 cc1 的 ridge 係數，把美股夜盤換算成隔日報酬變化
    pred, beta, mu, sd, ym = ridge_walk_forward(df, "cc1", 1)
    us_pending = bool(bars["^SOX"].index[-1] < df.index[-1])
    # 美股今晚未收盤時隔夜報酬為 0，不代表平靜，不得拿來比對類似盤面
    nb = neighbors(df, args.k, skip=("sox", "tsm", "vix") if us_pending else ())
    sample = nb.index
    analog = {}
    for t in ["gap", "cc1", "intraday"] + [f"fwd{h}" for h in HORIZONS]:
        r = df.loc[sample, t]
        order = r.sort_index()
        analog[t] = {**(dist_stats(r) or {}), "ci90": cluster_ci(order.values, episodes(order.index)),
                     "baseline": dist_stats(df[t])}

    levels = [0, 0.5, 1.0, 1.5, 2.0, 3.0]
    atr_now = float(df["atr_pct"].iloc[-1])
    grid = entry_grid(df, p, sorted(sample), args.stop_atr, args.hold, args.window, levels)
    sized = size_position(grid, atr_now, args.stop_atr, args.risk, args.kelly, args.max_total)
    long_grid = size_position(entry_grid(df, p, sorted(sample), None, 60, args.window, levels),
                              atr_now, None, args.risk, args.kelly, args.max_total)
    ref_0050 = float(bars["0050.TW"]["Close"].iloc[-1])
    ref_twii = float(twc.iloc[-1])
    for s in long_grid:
        s["limit_0050"] = ref_0050 * (1 - s["k_atr"] * atr_now)
        s["limit_twii"] = ref_twii * (1 - s["k_atr"] * atr_now)
    for s in sized:
        s["limit_0050"] = ref_0050 * (1 - s["k_atr"] * atr_now)
        s["limit_twii"] = ref_twii * (1 - s["k_atr"] * atr_now)
        s["stop_0050"] = s["limit_0050"] * (1 - args.stop_atr * atr_now)
        s["stop_twii"] = s["limit_twii"] * (1 - args.stop_atr * atr_now)

    recent = df.tail(750).iloc[:-1]
    buckets = [(-1, -.04, "< -4%"), (-.04, -.02, "-4% ~ -2%"), (-.02, -.005, "-2% ~ -0.5%"), (-.005, .005, "±0.5%"),
               (.005, .02, "+0.5% ~ +2%"), (.02, .04, "+2% ~ +4%"), (.04, 1, "> +4%")]
    scenario = []
    for lo, hi, label in buckets:
        g = recent[(recent["sox"] >= lo) & (recent["sox"] < hi)]
        scenario.append({"label": label, "n": int(len(g)), "gap": float(g["gap"].mean()) if len(g) else None,
                         "tw_next": float(g["tw_next"].mean()) if len(g) else None,
                         "tw_up": float((g["tw_next"] > 0).mean()) if len(g) else None,
                         "intraday": float(g["intraday"].mean()) if len(g) else None,
                         "implied_twii": ref_twii * (1 + float(g["tw_next"].mean())) if len(g) else None})

    # 圖表資料
    series = {}
    for tk in BREADTH + ["TSM", "^VIX", "TWD=X"]:
        if tk in bars:
            c = bars[tk]["Close"].dropna().tail(252)
            series[tk] = {"name": UNIVERSE[tk][0], "dates": [d.strftime("%Y-%m-%d") for d in c.index],
                          "close": [round(float(v), 4) for v in c.values]}
    tw_tail = twc.tail(504)
    tw_chart = {"dates": [d.strftime("%Y-%m-%d") for d in tw_tail.index], "close": [round(float(v), 2) for v in tw_tail],
                "ma20": [None if np.isnan(v) else round(float(v), 2) for v in twc.rolling(20).mean().tail(504)],
                "ma60": [None if np.isnan(v) else round(float(v), 2) for v in twc.rolling(60).mean().tail(504)],
                "ma200": [None if np.isnan(v) else round(float(v), 2) for v in twc.rolling(200).mean().tail(504)]}

    adr = None
    if all(t in bars for t in ("TSM", "2330.TW", "TWD=X")):
        s = (bars["TSM"]["Close"] * asof["TWD=X"].reindex(bars["TSM"].index).ffill() / 5
             / bars["2330.TW"]["Close"].reindex(bars["TSM"].index).ffill() - 1).dropna()
        adr = {"premium": float(s.iloc[-1]), "date": s.index[-1].strftime("%Y-%m-%d"),
               "percentile_1y": float((s.tail(252) < s.iloc[-1]).mean()), "mean_1y": float(s.tail(252).mean())}

    result = {
        "generated_at": datetime.now(ZoneInfo(TPE)).isoformat(timespec="seconds"),
        "last_tw_date": df.index[-1].strftime("%Y-%m-%d"),
        # 美股最後收盤早於台股最後收盤：今晚美股尚未反映，隔夜特徵為 0，須搭配情境表解讀
        "us_pending": us_pending,
        "last_us_date": bars["^SOX"].index[-1].strftime("%Y-%m-%d"),
        "ref": {"twii": ref_twii, "0050": ref_0050, "atr_pct": atr_now},
        "state": {k: (None if pd.isna(last_row[k]) else float(last_row[k])) for k in FEATURES + ["atr_pct"]},
        "state_percentile": {k: float((df[k].dropna() < last_row[k]).mean()) for k in FEATURES + ["atr_pct"] if pd.notna(last_row[k])},
        "missing_tickers": dropped,
        "quality": quality,
        "trends": trend_table(bars, live),
        "lead_lag": lead_lag(bars, twc),
        "validation": validation,
        "ridge_cc1_betas": {k: float(v / s) for (k, v), s in zip(beta.items(), sd)},
        "analog": analog,
        "analog_info": {"k": args.k, "episodes": int(episodes(sample).max() + 1),
                        "first": sample.min().strftime("%Y-%m-%d"), "last": sample.max().strftime("%Y-%m-%d"),
                        "closest": [d.strftime("%Y-%m-%d") for d in nb.index[:10]]},
        "factors": factor_table,
        "factor_missing": factor_missing + fund_problems,
        "factor_incremental": incremental,
        "event_study": events,
        "capex": capex,
        "earnings": earnings,
        "foreign_month_bn": df.attrs.get("foreign_month"),
        "margin_bn": df.attrs.get("margin_bn"),
        "entry_grid": sized,
        "entry_grid_long": long_grid,
        "sox_scenario": scenario,
        "params": {"stop_atr": args.stop_atr, "hold": args.hold, "window": args.window, "risk": args.risk,
                   "kelly": args.kelly, "max_total": args.max_total, "cost": ROUND_TRIP_COST},
        "conditions": [condition_study(df, p, c) for c in args.condition],
        "adr": adr,
        "flows": twse_flows(),
        "charts": {"tw": tw_chart, "series": series},
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: result[k] for k in result if k != "charts"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
