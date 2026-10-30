"""Grades the engine's earlier calls against what prices did afterwards.

This runs in the engine, not the server, for two reasons. It replays trades with
the same `sim_trade` that produced the calls, so a call and its grade cannot
disagree about how an order fills. And it needs dividend-adjusted prices, which
only the engine has; the server's `daily_price` is unadjusted and lazily filled.

A call only exists as a file here until it has matured. The grades live in the
database, so losing this directory loses the calls still pending and nothing else.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from market_engine import simulate

logger = logging.getLogger(__name__)

HORIZONS = {"short": {"hold": 20, "stop": 2.5}, "long": {"hold": 60, "stop": None}}
WINDOW = 5
#: The longest call matures in about 65 sessions. Files stay longer than that so
#: a stretch of failed imports does not lose grades: the server ignores repeats.
KEEP_DAYS = 130


def save_calls(directory: str, payload: dict, yahoo_symbols: dict[str, str]) -> None:
    calls = [
        {
            "sid": row["sid"],
            "yahoo": yahoo_symbols[row["sid"]],
            "market": row["market"],
            "sector": row["sector"],
            "close": row["close"],
            "risk_line": row["risk_line"],
            "hold_state": row["hold_state"],
            "short_entry_k": (row["short"]["entry"] or {}).get("k_atr"),
            "long_entry_k": (row["long"]["entry"] or {}).get("k_atr"),
        }
        for row in payload["recommendations"]
        if row["status"] == "ok"
    ]
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{payload['as_of']}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"as_of": payload["as_of"], "engine_version": payload["engine_version"], "calls": calls}),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def load_calls(directory: str, today: date | None = None) -> list[dict]:
    """Every call file still in its window. Older files are removed here."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    cutoff = (today or date.today()) - timedelta(days=KEEP_DAYS)
    files = []
    for path in sorted(folder.glob("*.json")):
        if date.fromisoformat(path.stem) < cutoff:
            path.unlink()
        else:
            files.append(json.loads(path.read_text(encoding="utf-8")))
    return files


def _label(entry_k) -> str:
    if entry_k is None:
        return "no_buy"
    return "buy_now" if entry_k == 0 else "buy_dip"


def grade(files: list[dict], prices: dict[str, pd.DataFrame], benchmark: pd.DataFrame) -> list[dict]:
    """One row per matured call and horizon.

    Every row carries what buying at the next open would have done, whatever the
    call said. Without it "不建議買" could never be judged: the only way to tell
    whether a warning was useful is what happened to those who ignored it.
    """
    rows = []
    for file in files:
        as_of = pd.Timestamp(file["as_of"])
        for call in file["calls"]:
            frame = prices.get(call["sid"])
            if frame is None or as_of not in frame.index or not call["close"]:
                continue
            i = frame.index.get_loc(as_of)
            o, l, c = frame["o"].values, frame["l"].values, frame["c"].values
            # The stored risk line is the stored ATR in another form, so a grade
            # uses the same range the call used rather than today's.
            atr = (1 - call["risk_line"] / call["close"]) / 2.5
            for horizon, rule in HORIZONS.items():
                hold, stop = rule["hold"], rule["stop"]
                # Graded once, when every case is decided, so a grade never changes.
                if i + WINDOW + hold >= len(c):
                    continue
                entry_k = call[f"{horizon}_entry_k"]
                _, at_market, _ = simulate.sim_trade(o, l, c, i, atr, 0, stop, hold, WINDOW)
                filled, followed, stopped = (False, None, False)
                if entry_k is not None:
                    filled, followed, stopped = simulate.sim_trade(o, l, c, i, atr, entry_k, stop, hold, WINDOW)
                bench_return = None
                if as_of in benchmark.index:
                    b = benchmark.index.get_loc(as_of)
                    if b + hold < len(benchmark):
                        bench_return = float(benchmark["c"].iloc[b + hold] / benchmark["o"].iloc[b + 1] - 1)
                rows.append(
                    {
                        "call_as_of": file["as_of"],
                        "engine_version": file["engine_version"],
                        "sid": call["sid"],
                        "sector": call["sector"],
                        "horizon": horizon,
                        "call": _label(entry_k),
                        "hold_state": call["hold_state"],
                        "filled": bool(filled),
                        "stopped": bool(stopped),
                        "followed_return": followed,
                        "market_entry_return": float(at_market),
                        "benchmark_return": bench_return,
                    }
                )
    return rows
