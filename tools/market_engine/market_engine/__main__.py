"""`python -m market_engine --out file.json`: one full daily run."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from market_engine import ENGINE_VERSION, SCHEMA_VERSION, scoring, tracking, universe

logger = logging.getLogger("market_engine")
TAIPEI = ZoneInfo("Asia/Taipei")

#: Below this share of stocks scored, an outage is more likely than bad luck,
#: and replacing yesterday's page with a mostly empty one helps nobody.
MIN_OK_SHARE = 0.6

#: What the calls are compared against: a fund a reader could simply have bought,
#: with dividends, unlike the price index.
BENCHMARK = "0050.TW"


def verified_index(raw: pd.DataFrame) -> pd.Series:
    """The index close, dropping today's bar until the market has closed.

    Stops the run if it disagrees with the exchange. Every comparison in the
    model is relative to this series, so a silent error here would pass through
    every result looking perfectly reasonable.
    """
    close = raw["^TWII"]["Close"].dropna()
    now = datetime.now(TAIPEI)
    if close.index[-1].date() >= now.date() and now.time() < time(14, 0):
        close = close.iloc[:-1]
    for date, official in universe.official_index_closes().items():
        if date > close.index[-1]:
            continue  # A partial bar we dropped, or a session Yahoo has not published yet.
        if date not in close.index:
            raise RuntimeError(f"official session {date.date()} is missing from Yahoo's index history")
        if abs(close.loc[date] / official - 1) > 0.001:
            raise RuntimeError(f"{date.date()}: Yahoo index {close.loc[date]:.2f} != official {official:.2f}")
    return close


def grade_earlier_calls(files: list[dict], raw: pd.DataFrame, market_close: pd.Series) -> list[dict]:
    """Grades what can be graded. Never allowed to cost today's recommendations."""
    if not files:
        return []
    try:
        benchmark = scoring.build_frame(
            scoring.clean(raw[BENCHMARK], market_close.index, "0050", "上市"), market_close
        )[1]
        prices = {}
        for call in {c["sid"]: c for file in files for c in file["calls"]}.values():
            try:
                bars = scoring.clean(raw[call["yahoo"]], market_close.index, call["sid"], call["market"])
                prices[call["sid"]] = scoring.build_frame(bars, market_close)[1]
            except Exception:  # A delisted stock must not stop the others being graded.
                logger.exception("Could not load prices for %s", call["sid"])
        return tracking.grade(files, prices, benchmark)
    except Exception:
        logger.exception("Could not grade earlier calls")
        return []


def run(params: scoring.Params, start: str, calls_dir: str) -> tuple[dict, dict[str, str]]:
    picks = universe.pick(universe.fetch_listing())
    files = tracking.load_calls(calls_dir)
    # A stock that has left the top five still has calls waiting to be graded.
    earlier = {c["yahoo"] for file in files for c in file["calls"]}
    raw = yf.download(
        sorted({p["yahoo"] for p in picks} | earlier | {"^TWII", BENCHMARK}),
        start=start,
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    market_close = verified_index(raw)
    rows, records = [], []
    for pick in picks:
        try:
            row, walk = scoring.score(pick, raw[pick["yahoo"]], market_close, params)
        except Exception as error:  # One bad ticker must not cost the other sixty their update.
            logger.exception("Could not score %s", pick["code"])
            row, walk = (
                {
                    "sid": pick["code"],
                    "name": pick["name"],
                    "market": pick["market"],
                    "sector": pick["sector"],
                    "sector_rank": pick["rank"],
                    "market_cap": float(pick["cap"]),
                    "status": "failed",
                    "status_detail": type(error).__name__,
                    "close": None,
                    "as_of": None,
                    "short": None,
                    "long": None,
                    "hold_state": "unknown",
                    "risk_line": None,
                },
                [],
            )
        rows.append(row)
        records += walk
        logger.info("%s %s %s %s", pick["sector"], pick["code"], row["status"], row["hold_state"])

    ok = sum(r["status"] == "ok" for r in rows)
    if ok < len(rows) * MIN_OK_SHARE:
        raise RuntimeError(f"only {ok} of {len(rows)} stocks scored; keeping the previous output")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": market_close.index[-1].strftime("%Y-%m-%d"),
        "recommendations": rows,
        "hold_backtest": scoring.summarize_walk_forward(records),
        "outcomes": grade_earlier_calls(files, raw, market_close),
    }
    return payload, {p["code"]: p["yahoo"] for p in picks}


def write_atomically(payload: dict, path: str) -> None:
    """The importer may read at any moment, so it must never see half a file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", default="2010-01-01")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    warnings.filterwarnings("ignore")
    try:
        calls_dir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "calls")
        payload, symbols = run(scoring.Params(), args.start, calls_dir)
        write_atomically(payload, args.out)
        # After the write: a call is only worth grading if readers could have seen it.
        tracking.save_calls(calls_dir, payload, symbols)
    except Exception:
        logger.exception("Run failed; the previous output was left in place")
        return 1
    logger.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
