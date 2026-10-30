"""Replays limit orders over history and turns the results into a decision.

Everything here is pure, so the rules that decide what a reader is told can be
pinned by tests without a network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Brokerage 0.1425% each way plus 0.3% stock transaction tax on the sale. ETFs
#: pay 0.1% tax, so this constant must not be reused for an ETF.
ROUND_TRIP_COST = 0.00585

#: A Taiwanese account buying US stocks through a broker's sub-brokerage, at a
#: typical 0.25% each way. A run handles one market, so `__main__` sets the
#: active rate once at startup.
US_ROUND_TRIP_COST = 0.005

#: Limit prices tried below the last close, in multiples of the average daily
#: range. Zero means buying at the next open.
LEVELS = [0, 0.5, 1.0, 1.5, 2.0, 3.0]

#: Below this many historical fills, the bad-case figure is one or two trades
#: and would read as far more certain than it is.
MIN_FILLS = 30


def episodes(dates, gap_days: int = 20) -> np.ndarray:
    """Group nearby dates into one episode.

    Neighbouring sessions share most of their future returns, so treating each
    as independent evidence makes a single rally look like many confirmations.
    """
    ds = pd.DatetimeIndex(sorted(dates))
    if len(ds) == 0:
        return np.array([], dtype=int)
    breaks = (np.diff(ds.values) > np.timedelta64(gap_days, "D")).astype(int)
    return np.cumsum(np.r_[0, breaks])


def cluster_ci(values, clusters, n: int = 2000, seed: int = 11) -> tuple[float | None, float | None]:
    """90% interval for the mean, resampling whole episodes."""
    v = pd.Series(np.asarray(values, float)).reset_index(drop=True)
    c = pd.Series(np.asarray(clusters)).reset_index(drop=True)
    groups = [g.dropna().values for _, g in v.groupby(c)]
    groups = [g for g in groups if len(g)]
    if len(groups) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    means = [
        np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))]).mean()
        for _ in range(n)
    ]
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def block_ci(values, block: int, n: int = 2000, seed: int = 7) -> tuple[float | None, float | None]:
    v = np.asarray(values, float)
    v = v[~np.isnan(v)]
    if len(v) < 10:
        return None, None
    rng = np.random.default_rng(seed)
    block = max(1, min(block, len(v) // 2))
    count = int(np.ceil(len(v) / block))
    starts = rng.integers(0, len(v) - block + 1, size=(n, count))
    means = np.array([np.concatenate([v[s : s + block] for s in row])[: len(v)].mean() for row in starts])
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def sim_trade(o, l, c, i: int, atr: float, k: float, stop_atr: float | None, hold: int, window: int):
    """Place a limit order after the close of bar `i`.

    Returns (filled, net return, stopped). `filled` is None when history ends
    before the outcome is known, so unfinished trades are never counted.

    Where the bars cannot say what came first, it assumes the worse order: a
    stop touched on the fill day counts as hit, and a gap through either level
    executes at the open.
    """
    n = len(c)
    if i + window >= n:
        return None, None, False
    limit = c[i] * (1 - k * atr)
    fill, price = None, None
    for j in range(i + 1, i + 1 + window):
        if k == 0 or o[j] <= limit:
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


def entry_grid(frame: pd.DataFrame, prices: pd.DataFrame, sample, stop_atr, hold: int, window: int) -> list[dict]:
    """Outcome of every limit level over the look-alike days and over all days."""
    pos = {d: i for i, d in enumerate(prices.index)}
    o, l, c = prices["o"].values, prices["l"].values, prices["c"].values
    atr = frame["atr_pct"]
    # Every third day: the baseline only has to be a stable average, and this
    # loop is most of the job's runtime.
    baseline_dates = frame.dropna(subset=["atr_pct", "dist200"]).index[:-1][::3]
    rows = []
    for k in LEVELS:
        rec = []
        for d in sample:
            filled, r, stopped = sim_trade(o, l, c, pos[d], atr.loc[d], k, stop_atr, hold, window)
            if filled is not None:
                rec.append((d, filled, r, stopped))
        base = []
        for d in baseline_dates:
            filled, r, _ = sim_trade(o, l, c, pos[d], atr.loc[d], k, stop_atr, hold, window)
            if filled:
                base.append(r)
        rd = pd.DataFrame(rec, columns=["d", "filled", "r", "stopped"])
        got = rd[rd["filled"] == True]  # noqa: E712 -- object column once None appears
        got_r = got["r"].astype(float)
        var = float(got_r.var()) if len(got) > 2 else None
        ev = float(got_r.mean()) if len(got) else None
        rows.append(
            {
                "k": k,
                "samples": int(len(rd)),
                "fills": int(len(got)),
                "fill_rate": float(len(got) / len(rd)) if len(rd) else 0.0,
                "ev": ev,
                "ci": cluster_ci(got.sort_values("d")["r"].astype(float).values, episodes(got["d"])) if len(got) else (None, None),
                "win": float((got_r > 0).mean()) if len(got) else None,
                "worst": float(got_r[got_r <= got_r.quantile(0.05)].mean()) if len(got) else None,
                "baseline_ev": float(np.mean(base)) if base else None,
                "kelly": float(ev / var) if var and ev is not None else None,
            }
        )
    return rows


def _good(row: dict) -> bool:
    lo = row["ci"][0]
    return row["fills"] >= MIN_FILLS and lo is not None and lo > 0 and row["ev"] > 0


def choose_entry(grid: list[dict]) -> dict | None:
    """The highest price at which buying has held up historically.

    The next lower level must agree, or have too few fills to say. Without that
    check, one lucky level surrounded by losing ones becomes the answer, and it
    is exactly what the six levels' noise produces most often.
    """
    for i, row in enumerate(grid):
        if not _good(row):
            continue
        nxt = grid[i + 1] if i + 1 < len(grid) else None
        if nxt is None or _good(nxt) or nxt["fills"] < MIN_FILLS:
            return row
    return None


def max_weight(row: dict, atr: float, stop_atr: float | None, verified: bool, risk: float, kelly_fraction: float, cap: float) -> float:
    """The largest share of capital this level justifies.

    Halved unless the method passed its out-of-sample check on this stock and
    the level beat the stock's own average. Most levels only earn the long-run
    drift, which is not a reason to bet as if there were an edge.
    """
    loss = stop_atr * atr if stop_atr is not None else abs(row["worst"] or 1.0)
    by_kelly = max(0.0, (row["kelly"] or 0.0) * kelly_fraction)
    full = verified and row["baseline_ev"] is not None and row["ci"][0] > row["baseline_ev"]
    return min(risk / loss, by_kelly, cap) * (1.0 if full else 0.5)


def hold_state(mean60, lo60, hi60, hi20) -> str:
    """How a holder's position looks, independent of what they paid.

    Deliberately not called buy or sell. The walk-forward check found that names
    graded weak went on to beat names graded strong, so `weak` may only be shown
    as "sell these first if you want less exposure", never as a sell signal,
    until `hold_backtest` says otherwise.
    """
    if None in (mean60, lo60, hi60, hi20):
        return "unknown"
    if hi60 < 0 or mean60 < 0 or hi20 < 0:
        return "weak"
    if lo60 > 0:
        return "strong"
    return "unclear"
