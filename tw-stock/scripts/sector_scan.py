#!/usr/bin/env python
"""類股期望值：各類股市值前五大，分別計算短線 20 日與長線 60 日的歷史條件期望值。

用法：sector_scan.py --out sector.json
市值 = 官方最新收盤 × 已發行股數。排名用今天的市值回看歷史，有倖存者偏誤，
所以分級要跟該股自身歷史比，不能只看報酬為正。
"""
import argparse
import json
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import market_scan as ms

warnings.filterwarnings("ignore")
H = {"User-Agent": "Mozilla/5.0"}

# (類股, TWSE 產業代碼, FinMind 上櫃產業名)
SECTORS = [
    ("半導體", "24", "半導體業"), ("記憶體", None, None), ("電腦及週邊", "25", "電腦及週邊設備業"),
    ("電子零組件", "28", "電子零組件業"), ("其他電子（含代工）", "31", "其他電子業"), ("通信網路", "27", "通信網路業"),
    ("電子通路", "29", "電子通路業"), ("光電", "26", "光電業"), ("建材營造（房市）", "14", "建材營造"),
    ("水泥", "01", "水泥工業"), ("金融保險", "17", "金融保險"), ("航運", "15", "航運業"), ("鋼鐵", "10", "鋼鐵工業"),
]
# 官方沒有記憶體分類，以下為人工定義的記憶體 IDM、模組與控制 IC 公司
MEMORY = {"2408", "2344", "2337", "8299", "3260", "4967", "3006", "2451"}
FEATURES = ["ret1", "ret5", "ret20", "dist20", "dist60", "dist200", "dd252", "rv20", "rel20", "rel60", "tw_dist200", "tw_rv20"]


def universe():
    listed = pd.DataFrame(requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", headers=H, timeout=40).json())
    px = pd.DataFrame(requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=H, timeout=40).json())
    px["close"] = pd.to_numeric(px["ClosingPrice"], errors="coerce")
    t = listed.merge(px[["Code", "close", "Date"]], left_on="公司代號", right_on="Code")
    t["shares"] = pd.to_numeric(t["已發行普通股數或TDR原股發行股數"], errors="coerce")
    rows = [{"code": r["公司代號"], "name": r["公司簡稱"].strip(), "ind": r["產業別"], "cap": r["close"] * r["shares"],
             "official_close": r["close"], "official_date": r["Date"], "yahoo": r["公司代號"] + ".TW", "market": "上市"}
            for _, r in t.iterrows() if len(r["公司代號"]) == 4 and pd.notna(r["close"]) and pd.notna(r["shares"])]
    q = pd.DataFrame(requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=H, timeout=40).json())
    q["close"] = pd.to_numeric(q["Close"], errors="coerce")
    q["shares"] = pd.to_numeric(q["Capitals"], errors="coerce")
    info = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInfo"}, headers=H, timeout=60).json()
    info = pd.DataFrame(info["data"])
    info = info[info["type"] == "tpex"]
    otc_ind = {n: info[info["industry_category"] == n]["stock_id"].unique().tolist() for _, _, n in SECTORS if n}
    code_ind = {c: n for n, cs in otc_ind.items() for c in cs}
    for _, r in q.iterrows():
        c = r["SecuritiesCompanyCode"]
        if len(c) == 4 and pd.notna(r["close"]) and pd.notna(r["shares"]) and (c in code_ind or c in MEMORY):
            rows.append({"code": c, "name": r["CompanyName"].strip(), "ind": code_ind.get(c), "cap": r["close"] * r["shares"],
                         "official_close": r["close"], "official_date": r["Date"], "yahoo": c + ".TWO", "market": "上櫃"})
    return pd.DataFrame(rows)


def pick(u):
    picks, memory = [], u[u["code"].isin(MEMORY)]
    otc_names = {}
    for sector, code, otc in SECTORS:
        if sector == "記憶體":
            g = memory
        else:
            g = u[((u["market"] == "上市") & (u["ind"] == code)) | ((u["market"] == "上櫃") & (u["ind"] == otc))]
            g = g[~g["code"].isin(MEMORY)]
        for rank, (_, r) in enumerate(g.sort_values("cap", ascending=False).head(5).iterrows(), 1):
            picks.append({"sector": sector, "rank": rank, **r.to_dict(), "members": int(len(g))})
    return picks


def clean(raw, calendar, code, market):
    d = raw.dropna(subset=["Close"]).copy()
    d = d.loc[d.index.intersection(calendar)]
    missing = calendar[calendar >= d.index[0]].difference(d.index)
    missing = missing[missing >= calendar[-1] - pd.Timedelta(days=365)]
    if market == "上市":
        for month in sorted({m.strftime("%Y%m") for m in missing}):
            official = ms.twse_stock_day(code, month)
            for m in missing:
                if m.strftime("%Y%m") == month and m in official:
                    o, h, l, c = official[m]
                    d.loc[m, ["Open", "High", "Low", "Close", "Adj Close"]] = [o, h, l, c, c]
    d = d.sort_index()
    prev = d["Close"].shift()
    for col in ("Open", "High", "Low"):
        d[col] = d[col].where(d[col] > 0, prev)
    return d.dropna(subset=["Open"])


def panel(d, twc):
    f = d["Adj Close"] / d["Close"]
    p = pd.DataFrame({"o": d["Open"] * f, "h": d["High"] * f, "l": d["Low"] * f, "c": d["Adj Close"]})
    c = p["c"]
    x = pd.DataFrame(index=p.index)
    x["ret1"], x["ret5"], x["ret20"] = c.pct_change(), c.pct_change(5), c.pct_change(20)
    for n in (20, 60, 200):
        x[f"dist{n}"] = c / c.rolling(n).mean() - 1
    x["dist_ma200"] = x["dist200"]
    x["dd252"] = c / c.rolling(252, min_periods=120).max() - 1
    x["rv20"] = np.log(c).diff().rolling(20).std() * np.sqrt(252)
    tw = twc.reindex(p.index)
    x["rel20"] = x["ret20"] - tw.pct_change(20)
    x["rel60"] = c.pct_change(60) - tw.pct_change(60)
    x["tw_dist200"] = tw / tw.rolling(200).mean() - 1
    x["tw_rv20"] = np.log(tw).diff().rolling(20).std() * np.sqrt(252)
    tr = pd.concat([p["h"] - p["l"], (p["h"] - c.shift()).abs(), (p["l"] - c.shift()).abs()], axis=1).max(axis=1)
    x["atr_pct"] = tr.rolling(14).mean() / c
    next_open = p["o"].shift(-1)
    for h in (20, 60, 250):
        x[f"fwd{h}"] = c.shift(-h) / next_open - 1
    return x.replace([np.inf, -np.inf], np.nan), p


def knn_oos(x, target, h, k=120, start="2016-01-01"):
    d = x.dropna(subset=FEATURES)
    a, y = d[FEATURES].values, d[target].values
    begin = max(d.index.searchsorted(pd.Timestamp(start)), 500)
    sse_m, sse_b, n = 0.0, 0.0, 0
    for i in range(begin, len(d), 5):  # 每 5 日取樣以節省時間，結果用來判斷方向已足夠
        if np.isnan(y[i]):
            continue
        end = i - h
        mask = ~np.isnan(y[:end])
        t, yt = a[:end][mask], y[:end][mask]
        if len(t) < k + 50:
            continue
        mu, sd = t.mean(0), t.std(0) + 1e-9
        dist = (((t - mu) / sd - (a[i] - mu) / sd) ** 2).sum(1)
        sse_m += (y[i] - yt[np.argpartition(dist, k)[:k]].mean()) ** 2
        sse_b += (y[i] - yt.mean()) ** 2
        n += 1
    return float(1 - sse_m / sse_b) if n >= 50 else None


LEVELS = [0, .5, 1.0, 1.5, 2.0, 3.0]
MIN_FILLS = 30


def plan(x, p, sample, stop, hold, args, atr):
    rows = ms.entry_grid(x, p, sample, stop, hold, args.window, LEVELS)
    return ms.size_position(rows, atr, stop, args.risk, args.kelly, args.max_single)


def choose_entry(grid):
    """由高價往低價找第一個合理價。

    合理 = 成交夠多且成交後期望值的 90% 區間下緣大於 0。
    下一檔也要成立（或下一檔成交太少無從判斷），避免單一檔位的雜訊被當成答案。
    """
    def fills(g):
        return g["fill_rate"] * g["samples"]

    def good(g):
        lo = g["ev_if_filled_ci90"][0]
        return fills(g) >= MIN_FILLS and lo is not None and lo > 0 and g["ev_if_filled"] > 0

    for i, g in enumerate(grid):
        if not good(g):
            continue
        nxt = grid[i + 1] if i + 1 < len(grid) else None
        if nxt is None or good(nxt) or fills(nxt) < MIN_FILLS:
            return g
    return None


def hold_action(m60, lo60, hi60, hi20):
    """已持有者的建議，只看類似盤面之後的報酬分佈，與買進成本無關。"""
    if None in (m60, lo60, hi60, hi20):
        return "資料不足"
    if hi60 < 0:
        return "脫手"
    if m60 < 0 or hi20 < 0:
        return "減碼"
    if lo60 > 0:
        return "續抱"
    return "可抱不加碼"


def hold_backtest(x, k):
    """逐步前推驗證持有建議：每個時點只用當時已知的資料，每 40 個交易日評估一次。"""
    d = x.dropna(subset=FEATURES)
    a = d[FEATURES].values
    y20, y60 = d["fwd20"].values, d["fwd60"].values
    begin = max(d.index.searchsorted(pd.Timestamp("2016-01-01")), 750)
    recs = []
    for i in range(begin, len(d) - 60, 40):
        end = i - 60
        mask = ~(np.isnan(y60[:end]) | np.isnan(y20[:end]))
        if mask.sum() < k + 50 or np.isnan(y60[i]) or np.isnan(y20[i]):
            continue
        t = a[:end][mask]
        mu, sd = t.mean(0), t.std(0) + 1e-9
        pick = np.argpartition((((t - mu) / sd - (a[i] - mu) / sd) ** 2).sum(1), k)[:k]
        pick = np.sort(pick)
        dates = d.index[:end][mask][pick]
        r60, r20 = y60[:end][mask][pick], y20[:end][mask][pick]
        ep = ms.episodes(dates)
        lo60, hi60 = ms.cluster_ci(r60, ep, n=200)
        _, hi20 = ms.cluster_ci(r20, ep, n=200)
        recs.append({"date": d.index[i], "action": hold_action(float(r60.mean()), lo60, hi60, hi20),
                     "ex20": float(y20[i] - y20[:end][mask].mean()), "ex60": float(y60[i] - y60[:end][mask].mean()),
                     "up60": float(y60[i] > 0)})
    return recs


def summarize_backtest(rows, market):
    recs = [r for row in rows for r in row.pop("_bt", [])]
    out = []
    if not recs:
        return out
    b = pd.DataFrame(recs)
    for action, g in b.groupby("action"):
        daily = g.groupby("date")["ex60"].mean().sort_index()
        ci = ms.block_ci(daily.values, 2)
        out.append({"market": market, "action": action, "n": int(len(g)), "ex20": float(g["ex20"].mean()),
                    "ex60": float(g["ex60"].mean()), "lo": ci[0], "hi": ci[1], "up60": float(g["up60"].mean())})
    return out


def score(pk, raw, twc, args):
    out = {k: pk[k] for k in ("sector", "rank", "code", "name", "market", "members")}
    out["cap_bn"] = float(pk["cap"] / 1e8)
    d = clean(raw, twc.index, pk["code"], pk["market"])
    x, p = panel(d, twc)
    last_close = float(d["Close"].iloc[-1])
    out.update(close=last_close, date=d.index[-1].strftime("%Y-%m-%d"), official_close=pk["official_close"])
    jumps = int((p["c"].pct_change().abs() > .2).sum())
    out["bad_jumps"] = jumps
    usable = x.dropna(subset=FEATURES)
    out["history_days"] = int(len(usable))
    if len(usable) < 750:
        out["status"] = "歷史不足"
        return out
    # 官方日期可能早於 Yahoo 最新收盤，只有同一天才比對
    if pk.get("official_date"):
        oy = int(pk["official_date"][:3]) + 1911
        od = pd.Timestamp(oy, int(pk["official_date"][3:5]), int(pk["official_date"][5:7]))
        if od in d.index and abs(d.loc[od, "Close"] / pk["official_close"] - 1) > .005:
            out["status"] = "資料核對失敗"
            return out
    out["status"] = "OK"
    last = usable.iloc[-1]
    hist = usable.iloc[:-1]
    mu, sd = hist[FEATURES].mean(), hist[FEATURES].std() + 1e-9
    dist = (((hist[FEATURES] - last[FEATURES]) / sd) ** 2).sum(axis=1) ** .5
    sample = sorted(dist.nsmallest(args.k).index)
    atr = float(x["atr_pct"].iloc[-1])
    out.update(atr=atr, d1=float(x["ret1"].iloc[-1]), d20=float(x["ret20"].iloc[-1]), dist200=float(x["dist200"].iloc[-1]),
               dd252=float(x["dd252"].iloc[-1]), rel20=float(x["rel20"].iloc[-1]), rv20=float(x["rv20"].iloc[-1]),
               episodes=int(ms.episodes(sample).max() + 1))
    entries = {}
    for name, stop, hold in [("short", args.stop_atr, 20), ("long", None, 60)]:
        grid = plan(x, p, sample, stop, hold, args, atr)
        g0 = grid[0]
        entries[name] = choose_entry(grid)
        out.update({f"{name}_ev": g0["ev_per_attempt"], f"{name}_lo": g0["ev_per_attempt_ci90"][0],
                    f"{name}_hi": g0["ev_per_attempt_ci90"][1], f"{name}_base": g0["baseline_ev_per_attempt"],
                    f"{name}_win": g0["win_if_filled"], f"{name}_worst": g0["cvar05_if_filled"], f"{name}_tier": g0["tier"],
                    f"{name}_size": g0["size"]})
    r = x.loc[sample, "fwd250"].dropna()
    r = r.sort_index()
    ci = ms.cluster_ci(r.values, ms.episodes(r.index)) if len(r) else [None, None]
    out.update(y1_ev=float(r.mean()) if len(r) else None, y1_win=float((r > 0).mean()) if len(r) else None, y1_lo=ci[0], y1_hi=ci[1],
               y1_base=float(x["fwd250"].mean()), y1_n=int(len(r)))
    out["r2_20"] = knn_oos(x, "fwd20", 20)
    out["r2_60"] = knn_oos(x, "fwd60", 60)
    for name, r2 in (("short", out["r2_20"]), ("long", out["r2_60"])):
        verified = r2 is not None and r2 > 0
        if out[f"{name}_tier"] == "A" and not verified:
            out[f"{name}_tier"] = "B"
            out[f"{name}_size"] *= .5
        g = entries[name]
        out[f"{name}_trust"] = "中" if verified else "低"
        if g is None:
            out[f"{name}_entry"] = None
            continue
        lo = g["ev_if_filled_ci90"][0]
        full = verified and g["baseline_ev_if_filled"] is not None and lo > g["baseline_ev_if_filled"]
        size = min(g["by_risk"], g["by_kelly"], args.max_single) * (1.0 if full else .5)
        out.update({f"{name}_entry": "buy", f"{name}_entry_k": g["k_atr"], f"{name}_entry_price": last_close * (1 - g["k_atr"] * atr),
                    f"{name}_entry_ev": g["ev_if_filled"], f"{name}_entry_lo": lo, f"{name}_entry_win": g["win_if_filled"],
                    f"{name}_entry_worst": g["cvar05_if_filled"], f"{name}_entry_fill": g["fill_rate"], f"{name}_entry_size": size})
    hs = {}
    for h in (20, 60):
        r = x.loc[sample, f"fwd{h}"].dropna().sort_index()
        hs[h] = (float(r.mean()), *ms.cluster_ci(r.values, ms.episodes(r.index)))
    out.update(hold_ev20=hs[20][0], hold_ev60=hs[60][0], hold_lo60=hs[60][1], hold_hi60=hs[60][2],
               hold_action=hold_action(hs[60][0], hs[60][1], hs[60][2], hs[20][2]))
    out["_bt"] = hold_backtest(x, args.k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--k", type=int, default=300)
    ap.add_argument("--stop-atr", type=float, default=2.5)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--max-single", type=float, default=0.10, help="單一個股資金上限")
    args = ap.parse_args()
    # Stocks pay 0.3% transaction tax; market_scan's default is the 0.1% ETF rate.
    ms.ROUND_TRIP_COST = 0.00585
    u = universe()
    picks = pick(u)
    tk = [p["yahoo"] for p in picks] + ["^TWII"]
    raw = yf.download(tk, start=args.start, auto_adjust=False, group_by="ticker", threads=True, progress=False)
    twc = raw["^TWII"]["Close"].dropna()
    rows = []
    for pk in picks:
        try:
            r = raw[pk["yahoo"]]
            rows.append(score(pk, r, twc, args))
        except Exception as e:
            rows.append({k: pk[k] for k in ("sector", "rank", "code", "name", "market", "members")} |
                        {"cap_bn": float(pk["cap"] / 1e8), "status": f"失敗：{str(e)[:80]}"})
        print(pk["sector"], pk["code"], pk["name"], rows[-1].get("status"), flush=True)
    backtest = summarize_backtest(rows, "台股")
    with open(args.out.replace(".json", "_bt.json"), "w", encoding="utf-8") as fh:
        json.dump(backtest, fh, ensure_ascii=False)
    keep = sorted({k for r in rows for k in r})
    with open(args.out.replace(".json", "_rows.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: r.get(k) for k in keep} for r in rows], fh, ensure_ascii=False)
    json.dump({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "stocks": rows,
               "params": vars(args)}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
