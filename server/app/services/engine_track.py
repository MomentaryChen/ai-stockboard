"""Turns graded calls into the track record readers see.

Daily calls overlap: two calls a day apart share nearly all of a 60-day outcome,
so 65 stocks a day for a month is far less evidence than its row count suggests.
All of the thresholds and intervals below therefore count call dates and
non-overlapping holding periods, never rows.
"""

from __future__ import annotations

import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EngineOutcome

HOLD_SESSIONS = {"short": 20, "long": 60}

#: Before this, an average is a few weeks of one market mood, so none is shown.
MIN_CALL_DATES = 20

#: Non-overlapping periods before saying whether a difference held up.
MIN_INDEPENDENT_PERIODS = 8


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _excess_interval(by_date: dict, hold: int, draws: int = 1000) -> tuple[float | None, float | None]:
    """90% interval for the average excess, resampling blocks of call dates."""
    dates = sorted(by_date)
    if len(dates) < hold * 2:
        return None, None
    series = [_mean(by_date[d]) for d in dates]
    count = max(1, len(series) // hold)
    rng = random.Random(7)  # Fixed so the page does not change between loads.
    means = []
    for _ in range(draws):
        sample: list[float] = []
        for _ in range(count):
            start = rng.randrange(0, len(series) - hold + 1)
            sample.extend(series[start : start + hold])
        means.append(_mean(sample))
    means.sort()
    return means[int(draws * 0.05)], means[int(draws * 0.95)]


def summarize_group(rows: list[EngineOutcome], horizon: str) -> dict:
    hold = HOLD_SESSIONS[horizon]
    dates = {row.call_as_of for row in rows}
    summary = {
        "calls": len(rows),
        "call_dates": len(dates),
        "independent_periods": len(dates) // hold,
        "ready": len(dates) >= MIN_CALL_DATES,
        "avg_return": None,
        "win_rate": None,
        "avg_benchmark": None,
        "avg_excess": None,
        "excess_lo": None,
        "excess_hi": None,
        "conclusion": "collecting",
    }
    if not summary["ready"]:
        return summary

    returns = [float(r.market_entry_return) for r in rows]
    summary["avg_return"] = _mean(returns)
    summary["win_rate"] = sum(v > 0 for v in returns) / len(returns)
    paired = [r for r in rows if r.benchmark_return is not None]
    summary["avg_benchmark"] = _mean([float(r.benchmark_return) for r in paired])
    by_date = defaultdict(list)
    for r in paired:
        by_date[r.call_as_of].append(float(r.market_entry_return) - float(r.benchmark_return))
    summary["avg_excess"] = _mean([v for values in by_date.values() for v in values])

    if summary["independent_periods"] >= MIN_INDEPENDENT_PERIODS:
        lo, hi = _excess_interval(by_date, hold)
        summary["excess_lo"], summary["excess_hi"] = lo, hi
        if lo is not None:
            summary["conclusion"] = "beat" if lo > 0 else "lagged" if hi < 0 else "no_difference"
    return summary


def load(db: Session) -> dict:
    rows = list(db.scalars(select(EngineOutcome)))
    groups = defaultdict(list)
    holds = defaultdict(list)
    for row in rows:
        groups[(row.horizon, row.call)].append(row)
        # Only the long horizon: the grade is built from 60-day outcomes.
        if row.horizon == "long":
            holds[row.hold_state].append(row)

    calls = []
    for horizon in HOLD_SESSIONS:
        for call in ("buy_now", "buy_dip", "no_buy"):
            members = groups.get((horizon, call), [])
            filled = [r for r in members if r.filled]
            calls.append(
                {
                    "horizon": horizon,
                    "call": call,
                    **summarize_group(members, horizon),
                    # What following the call itself did, for the levels that wait
                    # for a fill. Unfilled orders are neither a gain nor a loss.
                    "fill_rate": len(filled) / len(members) if members and call != "no_buy" else None,
                    "avg_followed_return": _mean([float(r.followed_return) for r in filled])
                    if len({r.call_as_of for r in members}) >= MIN_CALL_DATES
                    else None,
                }
            )
    return {
        "first_call": min((r.call_as_of for r in rows), default=None),
        "last_call": max((r.call_as_of for r in rows), default=None),
        "graded": len(rows),
        "min_call_dates": MIN_CALL_DATES,
        "calls": calls,
        "holds": [
            {"hold_state": state, **summarize_group(holds.get(state, []), "long")}
            for state in ("strong", "unclear", "weak")
        ],
    }
