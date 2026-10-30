#!/usr/bin/env python
"""使用者自選台股：沿用 sector_scan 的方法，名單在 custom_stocks.json。

用法：custom_scan.py --out custom.json
會產生 custom_rows.json。名單可含上市櫃股票與 ETF。
"""
import argparse
import json
import os
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

import market_scan as ms
import sector_scan as ss

LIST = os.path.join(os.path.dirname(__file__), "..", "custom_stocks.json")


def quotes():
    """官方最新收盤與名稱，ETF 也在這兩份行情內。"""
    out = {}
    for r in requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=ss.H, timeout=40).json():
        out[r["Code"].upper()] = {"name": r["Name"].strip(), "market": "上市", "yahoo": r["Code"] + ".TW",
                                  "official_close": pd.to_numeric(r["ClosingPrice"], errors="coerce"), "official_date": r["Date"]}
    for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=ss.H, timeout=40).json():
        c = r["SecuritiesCompanyCode"].upper()
        out.setdefault(c, {"name": r["CompanyName"].strip(), "market": "上櫃", "yahoo": r["SecuritiesCompanyCode"] + ".TWO",
                           "official_close": pd.to_numeric(r["Close"], errors="coerce"), "official_date": r["Date"]})
    for r in requests.get("https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics", headers=ss.H, timeout=40).json():
        c = r["SecuritiesCompanyCode"].upper()
        out.setdefault(c, {"name": r["CompanyName"].strip(), "market": "興櫃", "yahoo": r["SecuritiesCompanyCode"] + ".TWO",
                           "official_close": pd.to_numeric(r["LatestPrice"], errors="coerce"), "official_date": r["Date"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2010-01-01")
    a = ap.parse_args()
    args = SimpleNamespace(k=300, stop_atr=2.5, window=5, risk=0.01, kelly=0.25, max_single=0.10)
    codes = json.load(open(LIST, encoding="utf-8"))
    q = quotes()
    picks, rows = [], []
    for rank, c in enumerate(codes, 1):
        base = {"sector": "我的股票", "rank": rank, "code": c, "members": len(codes)}
        if c.upper() not in q:
            rows.append(base | {"name": c, "market": None, "cap_bn": None, "status": "查無此代號"})
            continue
        picks.append(base | q[c.upper()] | {"cap": 0.0})
    raw = yf.download([p["yahoo"] for p in picks] + ["^TWII"], start=a.start, auto_adjust=False, group_by="ticker",
                      threads=True, progress=False)
    twc = raw["^TWII"]["Close"].dropna()
    for pk in picks:
        try:
            rows.append(ss.score(pk, raw[pk["yahoo"]], twc, args))
        except Exception as e:
            rows.append({k: pk[k] for k in ("sector", "rank", "code", "name", "market", "members")} |
                        {"cap_bn": None, "status": f"失敗：{str(e)[:80]}"})
        print(pk["code"], pk["name"], rows[-1].get("status"), rows[-1].get("history_days"), flush=True)
    for r in rows:
        r.pop("_bt", None)
    rows.sort(key=lambda r: r["rank"])
    keep = sorted({k for r in rows for k in r})
    with open(a.out.replace(".json", "_rows.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: r.get(k) for k in keep} for r in rows], fh, ensure_ascii=False)
    json.dump({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "stocks": rows},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
