"""Scores one stock against the days in its own history that looked like today."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from market_engine import simulate, universe

FEATURES = ["ret1", "ret5", "ret20", "dist20", "dist60", "dist200", "dd252", "rv20", "rel20", "rel60", "mkt_dist200", "mkt_rv20"]


@dataclass(frozen=True)
class Params:
    k: int = 300  # Fewer look-alike days leave deep limit levels with too few fills.
    stop_atr: float = 2.5
    short_hold: int = 20
    long_hold: int = 60
    window: int = 5
    risk: float = 0.01
    kelly_fraction: float = 0.25
    max_single: float = 0.10
    min_history: int = 750


def clean(raw: pd.DataFrame, calendar: pd.DatetimeIndex, code: str, market: str) -> pd.DataFrame:
    """Align one stock to the index's sessions and repair known Yahoo faults.

    Yahoo has been seen to drop a real session and to invent a flat bar on a
    holiday. Taking the index's calendar removes the invented bars, and official
    data refills the missing ones. A dropped session is not harmless: the next
    day's change silently becomes a two-day change.
    """
    bars = raw.dropna(subset=["Close"]).copy()
    bars = bars.loc[bars.index.intersection(calendar)]
    missing = calendar[calendar >= bars.index[0]].difference(bars.index)
    missing = missing[missing >= calendar[-1] - pd.Timedelta(days=365)]
    if market == "上市":
        for month in sorted({m.strftime("%Y%m") for m in missing}):
            official = universe.twse_stock_day(code, month)
            for m in missing:
                if m.strftime("%Y%m") == month and m in official:
                    o, h, l, c = official[m]
                    bars.loc[m, ["Open", "High", "Low", "Close", "Adj Close"]] = [o, h, l, c, c]
    bars = bars.sort_index()
    previous = bars["Close"].shift()
    for column in ("Open", "High", "Low"):
        # Some old Yahoo bars carry a zero price, which would mean an infinite return.
        bars[column] = bars[column].where(bars[column] > 0, previous)
    return bars.dropna(subset=["Open"])


def build_frame(bars: pd.DataFrame, market_close: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features known after the close of each row, and returns from the next open.

    Every return starts at the next session's open because the close that
    produced a row's features can no longer be traded at.
    """
    factor = bars["Adj Close"] / bars["Close"]
    prices = pd.DataFrame(
        {"o": bars["Open"] * factor, "h": bars["High"] * factor, "l": bars["Low"] * factor, "c": bars["Adj Close"]}
    )
    c = prices["c"]
    x = pd.DataFrame(index=prices.index)
    x["ret1"], x["ret5"], x["ret20"] = c.pct_change(), c.pct_change(5), c.pct_change(20)
    for n in (20, 60, 200):
        x[f"dist{n}"] = c / c.rolling(n).mean() - 1
    x["dd252"] = c / c.rolling(252, min_periods=120).max() - 1
    x["rv20"] = np.log(c).diff().rolling(20).std() * np.sqrt(252)
    market = market_close.reindex(prices.index)
    x["rel20"] = x["ret20"] - market.pct_change(20)
    x["rel60"] = c.pct_change(60) - market.pct_change(60)
    x["mkt_dist200"] = market / market.rolling(200).mean() - 1
    x["mkt_rv20"] = np.log(market).diff().rolling(20).std() * np.sqrt(252)
    true_range = pd.concat(
        [prices["h"] - prices["l"], (prices["h"] - c.shift()).abs(), (prices["l"] - c.shift()).abs()], axis=1
    ).max(axis=1)
    x["atr_pct"] = true_range.rolling(14).mean() / c
    next_open = prices["o"].shift(-1)
    for h in (20, 60):
        x[f"fwd{h}"] = c.shift(-h) / next_open - 1
    return x.replace([np.inf, -np.inf], np.nan), prices


def _nearest(train: np.ndarray, point: np.ndarray, k: int) -> np.ndarray:
    mu, sd = train.mean(0), train.std(0) + 1e-9
    return np.argpartition((((train - mu) / sd - (point - mu) / sd) ** 2).sum(1), k)[:k]


def out_of_sample_r2(x: pd.DataFrame, target: str, h: int, k: int = 120, start: str = "2016-01-01") -> float | None:
    """Did look-alike days predict this stock better than its running average?

    `end = i - h` is what keeps it honest: a training row's outcome must already
    be known on day `i`. Sampled every five days because this only has to say
    which side of zero the stock falls on.
    """
    d = x.dropna(subset=FEATURES)
    a, y = d[FEATURES].values, d[target].values
    begin = max(d.index.searchsorted(pd.Timestamp(start)), 500)
    model, bench, n = 0.0, 0.0, 0
    for i in range(begin, len(d), 5):
        if np.isnan(y[i]):
            continue
        end = i - h
        mask = ~np.isnan(y[:end])
        train, outcome = a[:end][mask], y[:end][mask]
        if len(train) < k + 50:
            continue
        model += (y[i] - outcome[_nearest(train, a[i], k)].mean()) ** 2
        bench += (y[i] - outcome.mean()) ** 2
        n += 1
    return float(1 - model / bench) if n >= 50 else None


def _hold_from_sample(dates, r20: np.ndarray, r60: np.ndarray, boots: int) -> str:
    clusters = simulate.episodes(dates)
    lo60, hi60 = simulate.cluster_ci(r60, clusters, n=boots)
    _, hi20 = simulate.cluster_ci(r20, clusters, n=boots)
    return simulate.hold_state(float(r60.mean()), lo60, hi60, hi20)


def hold_walk_forward(x: pd.DataFrame, k: int) -> list[dict]:
    """Replays the holder's grade using only what was known at each date."""
    d = x.dropna(subset=FEATURES)
    a = d[FEATURES].values
    y20, y60 = d["fwd20"].values, d["fwd60"].values
    begin = max(d.index.searchsorted(pd.Timestamp("2016-01-01")), 750)
    records = []
    for i in range(begin, len(d) - 60, 40):
        end = i - 60
        mask = ~(np.isnan(y60[:end]) | np.isnan(y20[:end]))
        if mask.sum() < k + 50 or np.isnan(y60[i]) or np.isnan(y20[i]):
            continue
        chosen = np.sort(_nearest(a[:end][mask], a[i], k))
        state = _hold_from_sample(d.index[:end][mask][chosen], y20[:end][mask][chosen], y60[:end][mask][chosen], 200)
        records.append(
            {
                "date": d.index[i],
                "state": state,
                "ex20": float(y20[i] - y20[:end][mask].mean()),
                "ex60": float(y60[i] - y60[:end][mask].mean()),
                "up60": float(y60[i] > 0),
            }
        )
    return records


def summarize_walk_forward(records: list[dict]) -> list[dict]:
    if not records:
        return []
    table = pd.DataFrame(records)
    out = []
    for state, group in table.groupby("state"):
        daily = group.groupby("date")["ex60"].mean().sort_index()
        lo, hi = simulate.block_ci(daily.values, 2)
        out.append(
            {
                "state": state,
                "samples": int(len(group)),
                "excess_20d": float(group["ex20"].mean()),
                "excess_60d": float(group["ex60"].mean()),
                "excess_60d_lo": lo,
                "excess_60d_hi": hi,
                "up_rate_60d": float(group["up60"].mean()),
            }
        )
    return out


def _plan(frame, prices, sample, stop, hold, r2, atr, close, params: Params) -> dict:
    grid = simulate.entry_grid(frame, prices, sample, stop, hold, params.window)
    at_market = grid[0]
    verified = r2 is not None and r2 > 0
    entry = simulate.choose_entry(grid)
    return {
        "ev": at_market["ev"],
        "worst_5pct": at_market["worst"],
        "trust": "medium" if verified else "low",
        "entry": None
        if entry is None
        else {
            "k_atr": entry["k"],
            "price": close * (1 - entry["k"] * atr),
            "ev": entry["ev"],
            "ev_lo": entry["ci"][0],
            "win_rate": entry["win"],
            "worst_5pct": entry["worst"],
            "fill_rate": entry["fill_rate"],
            "max_weight": simulate.max_weight(
                entry, atr, stop, verified, params.risk, params.kelly_fraction, params.max_single
            ),
        },
    }


def score(pick: dict, raw: pd.DataFrame, market_close: pd.Series, params: Params) -> tuple[dict, list[dict]]:
    """One recommendation row, plus this stock's walk-forward records."""
    row = {
        "sid": pick["code"],
        "name": pick["name"],
        "market": pick["market"],
        "sector": pick["sector"],
        "sector_rank": pick["rank"],
        "market_cap": float(pick["cap"]),
        "status": "ok",
        "status_detail": None,
        "close": None,
        "as_of": None,
        "short": None,
        "long": None,
        "hold_state": "unknown",
        "risk_line": None,
    }
    bars = clean(raw, market_close.index, pick["code"], pick["market"])
    frame, prices = build_frame(bars, market_close)
    close = float(bars["Close"].iloc[-1])
    row.update(close=close, as_of=bars.index[-1].strftime("%Y-%m-%d"))

    usable = frame.dropna(subset=FEATURES)
    if len(usable) < params.min_history:
        row.update(status="insufficient_history", status_detail=f"{len(usable)} usable sessions")
        return row, []

    # The official figure can lag Yahoo by a session, so only the same date is comparable.
    date = pick["official_date"]
    official_date = pd.Timestamp(int(date[:3]) + 1911, int(date[3:5]), int(date[5:7]))
    if official_date in bars.index and abs(bars.loc[official_date, "Close"] / pick["official_close"] - 1) > 0.005:
        row.update(status="data_mismatch", status_detail=f"{official_date.date()} differs from the exchange close")
        return row, []

    last, history = usable.iloc[-1], usable.iloc[:-1]
    mu, sd = history[FEATURES].mean(), history[FEATURES].std() + 1e-9
    distance = (((history[FEATURES] - last[FEATURES]) / sd) ** 2).sum(axis=1)
    sample = sorted(distance.nsmallest(params.k).index)
    atr = float(frame["atr_pct"].iloc[-1])

    r2_short = out_of_sample_r2(frame, "fwd20", params.short_hold)
    r2_long = out_of_sample_r2(frame, "fwd60", params.long_hold)
    row["short"] = _plan(frame, prices, sample, params.stop_atr, params.short_hold, r2_short, atr, close, params)
    row["long"] = _plan(frame, prices, sample, None, params.long_hold, r2_long, atr, close, params)

    r20 = frame.loc[sample, "fwd20"].dropna().sort_index()
    r60 = frame.loc[sample, "fwd60"].dropna().sort_index()
    clusters = simulate.episodes(r60.index)
    lo60, hi60 = simulate.cluster_ci(r60.values, clusters)
    _, hi20 = simulate.cluster_ci(r20.values, simulate.episodes(r20.index))
    row["hold_state"] = simulate.hold_state(float(r60.mean()), lo60, hi60, hi20)
    row["risk_line"] = close * (1 - params.stop_atr * atr)
    return row, hold_walk_forward(frame, params.k)
