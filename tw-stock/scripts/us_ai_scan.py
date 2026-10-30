#!/usr/bin/env python
"""美股 AI 大廠期望值：沿用 sector_scan 的方法，各子類取市值前五大。

用法：us_ai_scan.py --out us_ai.json
成分是人工定義的候選清單，排名用 Yahoo 即時市值現算。
報酬以美元計，不含匯率；成本以複委託單邊 0.25% 估算。
"""
import argparse
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import market_scan as ms
import sector_scan as ss

GROUPS = {
    "AI 晶片": ["NVDA", "AVGO", "TSM", "AMD", "MU", "INTC", "QCOM", "MRVL", "ARM"],
    "半導體設備": ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
    "雲端與模型平台": ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
    "AI 伺服器與基建": ["DELL", "ANET", "SMCI", "VRT", "CSCO", "HPE"],
    "AI 軟體與應用": ["PLTR", "NOW", "CRM", "ADBE", "SNOW"],
}
COST = 0.005


def fetch_info():
    rows, problems = [], []
    for group, tickers in GROUPS.items():
        for t in tickers:
            try:
                i = yf.Ticker(t).info
                if not i.get("marketCap"):
                    raise RuntimeError("沒有市值")
                rows.append({"group": group, "code": t, "name": i.get("shortName") or t, "cap": float(i["marketCap"]),
                             "post": i.get("postMarketPrice"), "pre": i.get("preMarketPrice"),
                             "post_change": i.get("postMarketChangePercent"), "pre_change": i.get("preMarketChangePercent")})
            except Exception as e:
                problems.append(f"{t}：{str(e)[:80]}")
    return pd.DataFrame(rows), problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2010-01-01")
    a = ap.parse_args()
    ms.ROUND_TRIP_COST = COST
    args = SimpleNamespace(k=300, stop_atr=2.5, window=5, risk=0.01, kelly=0.25, max_single=0.10)
    info, problems = fetch_info()
    picks = []
    for group in GROUPS:
        g = info[info["group"] == group].sort_values("cap", ascending=False)
        for rank, (_, r) in enumerate(g.head(5).iterrows(), 1):
            picks.append({"sector": group, "rank": rank, "code": r["code"], "name": r["name"], "market": "美股",
                          "members": int(len(g)), "cap": r["cap"], "official_close": None, "official_date": None,
                          "yahoo": r["code"], "post": r["post"], "pre": r["pre"], "post_change": r["post_change"],
                          "pre_change": r["pre_change"]})
    raw = yf.download([p["yahoo"] for p in picks] + ["^GSPC"], start=a.start, auto_adjust=False, group_by="ticker",
                      threads=True, progress=False)
    spx = raw["^GSPC"]["Close"].dropna()
    now = datetime.now(ZoneInfo("America/New_York"))
    # 美股盤中或收盤確認前的當日 K 棒不可進回測
    if spx.index[-1].date() >= now.date() and now.time() < ms.time(16, 30):
        spx = spx.iloc[:-1]
    rows = []
    for pk in picks:
        extra = {k: pk[k] for k in ("post", "pre", "post_change", "pre_change")}
        try:
            r = ss.score(pk, raw[pk["yahoo"]], spx, args)
        except Exception as e:
            r = {k: pk[k] for k in ("sector", "rank", "code", "name", "market", "members")} | {
                "cap_bn": pk["cap"] / 1e8, "status": f"失敗：{str(e)[:80]}"}
        rows.append(r | extra)
        print(pk["sector"], pk["code"], r.get("status"), flush=True)
    backtest = ss.summarize_backtest(rows, "美股")
    with open(a.out.replace(".json", "_bt.json"), "w", encoding="utf-8") as fh:
        json.dump(backtest, fh, ensure_ascii=False)
    keep = sorted({k for r in rows for k in r})
    with open(a.out.replace(".json", "_rows.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: r.get(k) for k in keep} for r in rows], fh, ensure_ascii=False)
    json.dump({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "stocks": rows,
               "problems": problems, "last_us_date": spx.index[-1].strftime("%Y-%m-%d")},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
