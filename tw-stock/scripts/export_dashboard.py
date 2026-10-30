#!/usr/bin/env python
"""把 market_scan.py 的 JSON 轉成儀表板資料集（每個資料集一個 JSON 檔）。

用法：export_dashboard.py <scan.json> <輸出資料夾>
"""
import json
import os
import sys

r = json.load(open(sys.argv[1], encoding="utf-8"))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)


def save(name, rows):
    with open(os.path.join(out, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    print(name, len(rows))


st, ref, pct = r["state"], r["ref"], r["state_percentile"]
latest = next(t for t in r["trends"] if t["ticker"] == "^TWII")
flow = r["flows"][0]
save("snapshot", [{
    "as_of": r["generated_at"], "tw_date": r["last_tw_date"], "us_date": r["last_us_date"],
    "us_pending": r["us_pending"], "twii": ref["twii"], "etf": ref["0050"], "tw_d1": st["tw_ret1"],
    "tw_d20": st["tw_ret20"], "vs_ma200": st["dist_ma200"], "from_high": st["dd252"], "rv20": st["rv20"],
    "rv20_pct": pct["rv20"], "atr": ref["atr_pct"], "vix": st["vix_lvl"], "breadth": st["breadth"],
    "rsi": st["rsi14"], "foreign_bn": flow["外資及陸資(不含外資自營商)"], "trust_bn": flow["投信"],
    "total_bn": flow["合計"], "adr_premium": r["adr"]["premium"], "adr_pct": r["adr"]["percentile_1y"],
    "trend": latest["trend"], "analog_k": r["analog_info"]["k"], "analog_episodes": r["analog_info"]["episodes"],
}])
save("trends", [{k: t[k] for k in ("ticker", "name", "group", "date", "last", "d1", "d5", "d20", "d60", "vs_ma200",
                                   "from_high", "trend", "live_partial")} for t in r["trends"]])
tw = r["charts"]["tw"]
save("taiex", [{"date": d, "close": c, "ma20": a, "ma60": b, "ma200": e}
               for d, c, a, b, e in zip(tw["dates"], tw["close"], tw["ma20"], tw["ma60"], tw["ma200"])])
rows = []
for tk, s in r["charts"]["series"].items():
    if tk in ("^VIX", "TWD=X", "TSM"):
        continue
    base = s["close"][0]
    rows += [{"ticker": tk, "name": s["name"], "date": d, "index": round(c / base * 100, 2)}
             for d, c in zip(s["dates"], s["close"])]
save("indexed", rows)
save("lead_lag", [{"name": l["name"], "prev": l["corr_prev_250d"], "same": l["corr_same_250d"],
                   "beta_prev": l["beta_prev_250d"]} for l in r["lead_lag"]])
labels = {"gap": "隔日開盤跳空", "intraday": "開盤到收盤", "cc1": "隔日收盤", "fwd5": "未來 5 日",
          "fwd20": "未來 20 日", "fwd60": "未來 60 日"}
save("validation", [{"target": labels[k], "r2": v["ridge"]["r2_oos"], "ic": v["ridge"]["ic"],
                     "ic_lo": v["ridge"]["ic_ci90"][0], "ic_hi": v["ridge"]["ic_ci90"][1],
                     "hit": v["ridge"]["hit"], "base_up": v["ridge"]["base_up"],
                     "tradable": k not in ("gap", "cc1")} for k, v in r["validation"].items()])
save("analog", [{"horizon": labels[k], "mean": r["analog"][k]["mean"], "lo": r["analog"][k]["ci90"][0],
                 "hi": r["analog"][k]["ci90"][1], "win": r["analog"][k]["win"], "p05": r["analog"][k]["p05"],
                 "base": r["analog"][k]["baseline"]["mean"], "base_win": r["analog"][k]["baseline"]["win"]}
                for k in ("fwd5", "fwd20", "fwd60")])
save("scenario", r["sox_scenario"])


def grid(rows, plan):
    return [{"plan": plan, "k": g["k_atr"], "twii": g["limit_twii"], "etf": g["limit_0050"], "fill": g["fill_rate"],
             "ev_fill": g["ev_if_filled"], "ev": g["ev_per_attempt"], "lo": g["ev_per_attempt_ci90"][0],
             "hi": g["ev_per_attempt_ci90"][1], "base": g["baseline_ev_per_attempt"], "win": g["win_if_filled"],
             "cvar": g["cvar05_if_filled"], "tier": g["tier"], "size": g["size"]} for g in rows]


save("entry", grid(r["entry_grid_long"], "長線 60 日") + grid(r["entry_grid"], "短線 20 日含停損"))
save("flows", [{"date": f["date"], "foreign": f["外資及陸資(不含外資自營商)"], "trust": f["投信"], "total": f["合計"]}
               for f in reversed(r["flows"])])


UNIT = {"rate3m_chg60": "{:+.2f} 個百分點", "y10_chg20": "{:+.2f} 個百分點", "curve": "{:+.2f} 個百分點",
        "unemp_chg6": "{:+.1f} 個百分點", "vix_level": "{:.1f}", "vix_term": "{:.2f}", "per_2330": "{:.1f} 倍",
        "pbr_2330": "{:.1f} 倍", "foreign_oi": "{:,.0f} 口"}
FLAGS = {"qtr_end", "msci_window", "broke_ma120"}


def reading(f):
    v, k = f["value"], f["key"]
    if v is None:
        return "無資料"
    if k in FLAGS:
        return "是" if v > 0 else "否"
    return UNIT[k].format(v) if k in UNIT else f"{v * 100:+.1f}%"


rows = []
for f in r["factors"]:
    t60, t20 = f.get("ic60") or {}, f.get("ic20") or {}
    rows.append({"key": f["key"], "category": f["category"], "label": f["label"], "star": f["star"], "reading": reading(f),
                 "pct": f.get("pct"), "zone": f.get("zone"), "ic20": t20.get("ic"), "ic60": t60.get("ic"),
                 "lo60": t60.get("lo"), "hi60": t60.get("hi"), "same60": f.get("fwd60_same"), "base60": f.get("fwd60_base"),
                 "passed": "、".join(f"{h} 日" for h in f.get("passed", [])), "verdict": f["verdict"], "signal": f.get("signal")})
save("factors", rows)
names = {"intraday": "開盤到收盤", "fwd5": "未來 5 日", "fwd20": "未來 20 日", "fwd60": "未來 60 日"}
save("incremental", [{"target": names[x["target"]], "n": x["n"], "base": x["base"]["r2_oos"], "extended": x["extended"]["r2_oos"]}
                     for x in r["factor_incremental"]])
save("event_study", r["event_study"])
latest = {}
for c in r["capex"]:
    latest.setdefault(c["ticker"], []).append(c)
capex = []
for t, q in latest.items():
    q = sorted(q, key=lambda x: x["quarter"])
    yoy = q[-1]["capex_bn"] / q[-5]["capex_bn"] - 1 if len(q) >= 5 else None
    capex.append({"ticker": t, "quarter": q[-1]["quarter"], "capex_bn": q[-1]["capex_bn"], "yoy": yoy})
save("capex", capex)
save("earnings", r["earnings"])
