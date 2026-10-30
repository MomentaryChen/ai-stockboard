"""`python -m market_engine --out file.json`: one full daily run."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from market_engine import ENGINE_VERSION, SCHEMA_VERSION, scoring, simulate, tracking, universe

logger = logging.getLogger("market_engine")
TAIPEI = ZoneInfo("Asia/Taipei")

#: Below this share of stocks scored, an outage is more likely than bad luck,
#: and replacing yesterday's page with a mostly empty one helps nobody.
MIN_OK_SHARE = 0.6

#: Longer than any market closure, so an older last bar means the feed has stalled.
MAX_INDEX_AGE_DAYS = 7


@dataclass(frozen=True)
class Region:
    name: str
    index: str
    # What the calls are compared against: a fund a reader could simply have
    # bought, with dividends, unlike the price index.
    benchmark: str
    benchmark_code: str
    benchmark_market: str
    calls_folder: str
    timezone: ZoneInfo
    # When today's bar is final, including the delay before Yahoo settles it.
    close_at: time


REGIONS = {
    "tw": Region("tw", "^TWII", "0050.TW", "0050", "上市", "calls", TAIPEI, time(14, 0)),
    "us": Region("us", "^GSPC", "SPY", "SPY", "美股", "calls-us", ZoneInfo("America/New_York"), time(16, 30)),
}


def verified_index(raw: pd.DataFrame, region: Region) -> pd.Series:
    """The index close, dropping today's bar until the market has closed.

    Stops the run if it disagrees with the exchange. Every comparison in the
    model is relative to this series, so a silent error here would pass through
    every result looking perfectly reasonable. No free official close exists for
    the US index, so all that can be checked there is that the data is current.
    """
    close = raw[region.index]["Close"].dropna()
    now = datetime.now(region.timezone)
    if close.index[-1].date() >= now.date() and now.time() < region.close_at:
        close = close.iloc[:-1]
    if (now.date() - close.index[-1].date()).days > MAX_INDEX_AGE_DAYS:
        raise RuntimeError(f"newest {region.index} bar is {close.index[-1].date()}")
    if region.name != "tw":
        return close
    for date, official in universe.official_index_closes().items():
        if date > close.index[-1]:
            continue  # A partial bar we dropped, or a session Yahoo has not published yet.
        if date not in close.index:
            raise RuntimeError(f"official session {date.date()} is missing from Yahoo's index history")
        if abs(close.loc[date] / official - 1) > 0.001:
            raise RuntimeError(f"{date.date()}: Yahoo index {close.loc[date]:.2f} != official {official:.2f}")
    return close


def grade_earlier_calls(files: list[dict], raw: pd.DataFrame, market_close: pd.Series, region: Region) -> list[dict]:
    """Grades what can be graded. Never allowed to cost today's recommendations."""
    if not files:
        return []
    try:
        benchmark = scoring.build_frame(
            scoring.clean(raw[region.benchmark], market_close.index, region.benchmark_code, region.benchmark_market),
            market_close,
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


def run(params: scoring.Params, start: str, calls_dir: str, region: Region) -> tuple[dict, dict[str, str]]:
    picks = universe.pick(universe.fetch_listing()) if region.name == "tw" else universe.us_picks()
    files = tracking.load_calls(calls_dir)
    # A stock that has left the top five still has calls waiting to be graded.
    earlier = {c["yahoo"] for file in files for c in file["calls"]}
    raw = yf.download(
        sorted({p["yahoo"] for p in picks} | earlier | {region.index, region.benchmark}),
        start=start,
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    market_close = verified_index(raw, region)
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
                    "extended_price": pick.get("extended_price"),
                    "extended_change": pick.get("extended_change"),
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
        "region": region.name,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": market_close.index[-1].strftime("%Y-%m-%d"),
        "recommendations": rows,
        "hold_backtest": scoring.summarize_walk_forward(records),
        "outcomes": grade_earlier_calls(files, raw, market_close, region),
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
    parser.add_argument("--region", choices=sorted(REGIONS), default="tw")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    warnings.filterwarnings("ignore")
    try:
        region = REGIONS[args.region]
        if region.name == "us":
            simulate.ROUND_TRIP_COST = simulate.US_ROUND_TRIP_COST
        calls_dir = os.path.join(os.path.dirname(os.path.abspath(args.out)), region.calls_folder)
        payload, symbols = run(scoring.Params(), args.start, calls_dir, region)
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
