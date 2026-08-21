"""Did the 四大買賣點 signal actually work? Replayed over bars we already hold.

The board says "Buy" today. This module is what lets it also say how often that
verdict has been right, computed from `daily_price` alone -- no extra trip to
the exchange, because the history is already landed.

Three questions get answered, because they are different questions:

  * **Was the signal right?** For every Buy/Sell the engine produced, what did
    the price do over the next 5 / 10 / 20 trading days. This assumes no
    trading discipline at all, so it measures the signal rather than a strategy
    built on it.
  * **Would trading it have paid?** A long-only simulation: go in on a Buy, out
    on a Sell, and compare the result with having bought on day one and done
    nothing. This is the number that decides whether the signal is worth acting
    on, and it is very often the one that says no.
  * **Was it better than not looking?** The same forward returns computed over
    *every* judged day, signal or not. A hit rate read on its own is unusable --
    the reader has to assume a base rate, and they will assume 50 %. See
    `BaselineStats`; the subtraction is done for them in `Edge`.

Three things keep the answer honest, and each is the kind of detail that
silently turns a backtest into a advertisement:

  * **No look-ahead.** A signal is computed from bar `i`'s close, so it cannot
    be traded until bar `i+1` opens. Entering at the same close that produced
    the signal is the classic way to backtest a fantasy; every order here is
    placed after a close and filled at the next open.
  * **Unfinished business is reported, not hidden.** A Buy four days before the
    end of the window has no 20-day outcome yet. Those are counted as `pending`
    and excluded from the rate, rather than being scored as though the horizon
    had elapsed. An open position at the end is likewise reported separately
    from closed trades.
  * **The same engine.** Signals come from `traditional.best_four_point`, the
    exact function the card on the page calls. A backtest of a reimplementation
    measures the reimplementation.

`WINDOW_BARS` is what makes replaying every day affordable -- see the constant.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from app.models import DailyPrice
from app.services.analysis import traditional
from app.services.analysis.traditional import MIN_SAMPLES_FOR_BFP, _CachedStock

#: Forward horizons, in trading days, that a signal is scored over.
HORIZONS = (5, 10, 20)

#: How many trailing bars one verdict is computed from.
#:
#: Not a tuning knob -- it is the point past which `BestFourPoint` provably
#: cannot see, so truncating there changes no verdict while turning an O(n^2)
#: replay into an O(n) one. Its deepest reach is the 3/6-day bias pivot:
#: `ma_bias_ratio_pivot` samples the last 5 bias values, each needing an MA6,
#: so 10 bars. `continuous(moving_average(price, 3)) == 1` is decided by three
#: MA3 values, so 5. `best_buy_1` / `best_buy_2` read two bars.
#:
#: 10 would do. 40 is kept because twstock's `moving_average` is O(n*w) over
#: whatever it is handed, so the saving is already banked at this size, and the
#: margin means a rule change upstream that reaches slightly further back does
#: not quietly start returning different verdicts here than on the stock page.
#: `test_backtest.py` pins the equivalence rather than trusting this comment.
WINDOW_BARS = 40

#: Fewest usable bars worth replaying at all. `best_four_point` needs
#: MIN_SAMPLES_FOR_BFP before it will judge anything, and a window that leaves
#: only a handful of judged days produces rates from two or three samples that
#: read as authoritative and are not.
MIN_BARS_FOR_BACKTEST = MIN_SAMPLES_FOR_BFP + max(HORIZONS) + 10


@dataclass(frozen=True)
class SignalRecord:
    """One historical Buy or Sell, and what happened after it."""

    date: datetime.date
    signal: str  # buy | sell
    close: float
    reasons: list[str]
    #: Horizon (trading days) -> return, as a fraction. Missing when the window
    #: ends before the horizon does.
    forward: dict[int, float]


@dataclass(frozen=True)
class Trade:
    """One completed round trip: in on a Buy, out on a Sell."""

    entry_date: datetime.date
    entry_price: float
    exit_date: datetime.date
    exit_price: float
    holding_days: int  # trading days held

    @property
    def profit(self) -> float:
        return self.exit_price / self.entry_price - 1.0


@dataclass(frozen=True)
class HorizonStats:
    """Hit rate for one signal type over one horizon."""

    horizon: int
    samples: int
    wins: int
    #: Signals too recent to have run their full horizon yet. Deliberately kept
    #: out of `samples` -- scoring them would mean scoring a partial outcome as
    #: a final one.
    pending: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None


def _rate(wins: int, samples: int) -> float | None:
    return wins / samples if samples else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def replay_signals(
    rows: list[DailyPrice], rule_set: str = traditional.DEFAULT_RULE_SET
) -> list[tuple[int, str, list[str]]]:
    """Every day's verdict, as it would have read on that day.

    Returns (bar index, signal, reasons) for the days that produced a Buy or a
    Sell. `hold` days are dropped: they are the overwhelming majority and
    nothing downstream needs them.

    Each verdict sees only bars up to and including its own -- that is the whole
    point, and the trailing slice is what enforces it.
    """
    verdicts: list[tuple[int, str, list[str]]] = []
    for i in range(MIN_SAMPLES_FOR_BFP - 1, len(rows)):
        window = rows[max(0, i - WINDOW_BARS + 1) : i + 1]
        result = traditional.best_four_point(_CachedStock(window), rule_set)
        if result.signal in ("buy", "sell"):
            verdicts.append((i, result.signal, result.reasons))
    return verdicts


def _forward_returns(closes: list[float], index: int) -> dict[int, float]:
    base = closes[index]
    if base <= 0:
        return {}
    out: dict[int, float] = {}
    for horizon in HORIZONS:
        target = index + horizon
        if target < len(closes):
            out[horizon] = closes[target] / base - 1.0
    return out


def _horizon_stats(records: list[SignalRecord], signal: str) -> list[HorizonStats]:
    """Hit rate per horizon for one signal type.

    A Buy is right when the price went up; a Sell is right when it went down --
    a Sell is a get-out, so a fall after it is the signal working, not a loss.
    Zero counts as a miss for both: a signal that moved nothing was not worth
    acting on.
    """
    mine = [r for r in records if r.signal == signal]
    stats = []
    for horizon in HORIZONS:
        returns = [r.forward[horizon] for r in mine if horizon in r.forward]
        pending = len(mine) - len(returns)
        wins = sum(
            1 for value in returns if (value > 0 if signal == "buy" else value < 0)
        )
        stats.append(
            HorizonStats(
                horizon=horizon,
                samples=len(returns),
                wins=wins,
                pending=pending,
                win_rate=_rate(wins, len(returns)),
                average_return=_mean(returns),
                median_return=_median(returns),
            )
        )
    return stats


def _max_drawdown(equity: list[float]) -> float:
    """Deepest peak-to-trough fall of the equity curve, as a negative fraction.

    Reported because a strategy and buy-and-hold can finish at the same number
    having put the holder through very different weeks, and that difference is
    what decides whether a strategy is actually followable.
    """
    peak = equity[0] if equity else 1.0
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


@dataclass(frozen=True)
class Simulation:
    """The long-only run: in on a Buy, out on a Sell, one position at a time."""

    trades: list[Trade]
    #: Daily (date, strategy, buy-and-hold), both starting at 1.0 on the first
    #: judged bar. Carried on shared points so the chart overlays them without
    #: having to align two series, and so the two can never silently be drawn
    #: over different date ranges.
    equity: list[tuple[datetime.date, float, float]]
    strategy_return: float
    buy_hold_return: float
    max_drawdown: float
    buy_hold_max_drawdown: float
    #: Entry date and price of a position still open when the window ended, or
    #: None. Its unrealised result is inside `strategy_return` but it is not a
    #: completed trade, so it is never counted in the win rate.
    open_entry: tuple[datetime.date, float] | None
    exposure: float  # fraction of judged days spent holding


def simulate(
    rows: list[DailyPrice],
    verdicts: list[tuple[int, str, list[str]]],
    start: int,
) -> Simulation:
    """Trade the verdicts, filling every order at the next bar's open.

    `start` is the first bar the engine was able to judge. Both the strategy
    and its buy-and-hold benchmark are measured from that bar's close, so the
    comparison covers exactly the same days -- a benchmark that starts earlier
    is measuring a different period and flatters whichever side got the better
    stretch.
    """
    dates = [r.date for r in rows]
    closes = [float(r.close) for r in rows]
    opens = [float(r.open) for r in rows]

    by_index = {index: signal for index, signal, _ in verdicts}

    base_close = closes[start] if start < len(closes) else 0.0

    cash, shares = 1.0, 0.0
    entry: tuple[datetime.date, float, int] | None = None
    pending: str | None = None
    trades: list[Trade] = []
    equity: list[tuple[datetime.date, float, float]] = []
    held_days = 0

    for i in range(start, len(rows)):
        # Yesterday's order, filled at today's open. This is the only place a
        # position changes, which is what makes the no-look-ahead property a
        # property of the loop rather than of every branch below.
        if pending == "buy" and shares == 0.0 and opens[i] > 0:
            shares, cash = cash / opens[i], 0.0
            entry = (dates[i], opens[i], i)
        elif pending == "sell" and shares > 0.0:
            cash, shares = shares * opens[i], 0.0
            if entry is not None:
                trades.append(
                    Trade(
                        entry_date=entry[0],
                        entry_price=entry[1],
                        exit_date=dates[i],
                        exit_price=opens[i],
                        holding_days=i - entry[2],
                    )
                )
            entry = None
        pending = None

        if shares > 0.0:
            held_days += 1
        equity.append(
            (
                dates[i],
                cash + shares * closes[i],
                (closes[i] / base_close) if base_close > 0 else 1.0,
            )
        )

        # Today's close produces today's verdict, which becomes tomorrow's
        # order. A Buy while already long and a Sell while flat are both no-ops.
        signal = by_index.get(i)
        if signal == "buy" and shares == 0.0:
            pending = "buy"
        elif signal == "sell" and shares > 0.0:
            pending = "sell"

    judged = len(equity)
    final = equity[-1][1] if equity else 1.0

    return Simulation(
        trades=trades,
        equity=equity,
        strategy_return=final - 1.0,
        buy_hold_return=(closes[-1] / base_close - 1.0) if base_close > 0 else 0.0,
        max_drawdown=_max_drawdown([value for _, value, _ in equity]),
        buy_hold_max_drawdown=_max_drawdown([value for _, _, value in equity]),
        open_entry=(entry[0], entry[1]) if entry else None,
        exposure=(held_days / judged) if judged else 0.0,
    )


@dataclass(frozen=True)
class BaselineStats:
    """What an *unconditional* position over the same days would have scored.

    This is the number that makes every other number readable. A Buy hit rate
    of 55 % sounds like a signal until you learn that 56 % of all judged days
    in the same window closed higher 20 bars later -- at which point the rule
    is worse than owning the stock and never looking at it. Every published
    win rate that omits its base rate is making the reader supply an assumption
    they have no way to check, and the assumption they supply is 50 %, which is
    almost never what the window actually did.

    Measured over exactly the days the engine was able to judge, so the signal
    and its reference see the same market.
    """

    horizon: int
    samples: int
    #: Kept alongside `up_rate` because `pool` sums counts across stocks;
    #: reconstructing this from the rate would round, and the pooled rate is
    #: the one number the whole exercise exists to produce.
    ups: int
    #: Fraction of judged days whose forward return was positive.
    up_rate: float | None
    average_return: float | None
    median_return: float | None


@dataclass(frozen=True)
class Edge:
    """Signal minus baseline. Zero or below means the rule added nothing.

    Kept as its own type rather than left for the caller to subtract, because
    the sell side is not the arithmetic anyone guesses: a Sell is right when
    the price *falls*, so its reference is the fraction of days that fell
    (`1 - up_rate`), and its excess return is the drop it avoided relative to
    an average day -- baseline minus signal, not signal minus baseline.
    """

    horizon: int
    #: Buy win rate - baseline up rate. Positive = the rule beat the coin.
    buy_edge: float | None
    #: Sell win rate - baseline down rate.
    sell_edge: float | None
    #: Buy mean return - baseline mean return.
    buy_excess_return: float | None
    #: Baseline mean return - Sell mean return: what getting out avoided.
    sell_excess_return: float | None


def _baseline_stats(closes: list[float], start: int) -> list[BaselineStats]:
    """Forward returns of *every* judged day, signal or not."""
    stats = []
    for horizon in HORIZONS:
        returns = [
            closes[i + horizon] / closes[i] - 1.0
            for i in range(start, len(closes) - horizon)
            if closes[i] > 0
        ]
        ups = sum(1 for value in returns if value > 0)
        stats.append(
            BaselineStats(
                horizon=horizon,
                samples=len(returns),
                ups=ups,
                up_rate=_rate(ups, len(returns)),
                average_return=_mean(returns),
                median_return=_median(returns),
            )
        )
    return stats


def _sub(left: float | None, right: float | None) -> float | None:
    """None when either side is missing -- an edge over an unknown is unknown."""
    return None if left is None or right is None else left - right


def _edges(
    buy_stats: list[HorizonStats],
    sell_stats: list[HorizonStats],
    baseline: list[BaselineStats],
) -> list[Edge]:
    buys = {s.horizon: s for s in buy_stats}
    sells = {s.horizon: s for s in sell_stats}
    base = {s.horizon: s for s in baseline}

    edges = []
    for horizon in HORIZONS:
        reference = base[horizon]
        down_rate = None if reference.up_rate is None else 1.0 - reference.up_rate
        edges.append(
            Edge(
                horizon=horizon,
                buy_edge=_sub(buys[horizon].win_rate, reference.up_rate),
                sell_edge=_sub(sells[horizon].win_rate, down_rate),
                buy_excess_return=_sub(
                    buys[horizon].average_return, reference.average_return
                ),
                sell_excess_return=_sub(
                    reference.average_return, sells[horizon].average_return
                ),
            )
        )
    return edges


@dataclass(frozen=True)
class BacktestReport:
    sid: str
    rule_set: str
    start: datetime.date
    end: datetime.date
    bars: int  # usable bars in the window
    judged_days: int  # days the engine was able to produce a verdict for
    signals: list[SignalRecord]
    buy_stats: list[HorizonStats]
    sell_stats: list[HorizonStats]
    #: The same horizons scored over every judged day, signal or not. Read the
    #: signal rates against these, never against 50 %.
    baseline: list[BaselineStats]
    #: Signal minus baseline, precomputed so no caller has to get the sell
    #: side's arithmetic right on its own.
    edges: list[Edge]
    simulation: Simulation


class NotEnoughBars(ValueError):
    """Fewer usable bars than `MIN_BARS_FOR_BACKTEST`. Becomes a 422 / a note."""

    def __init__(self, available: int) -> None:
        super().__init__(
            f"Need at least {MIN_BARS_FOR_BACKTEST} usable trading days to "
            f"backtest; have {available}"
        )
        self.available = available


def run(
    sid: str,
    rows: list[DailyPrice],
    rule_set: str = traditional.DEFAULT_RULE_SET,
) -> BacktestReport:
    """Replay `rows` and score every verdict. Pure: no database, no network."""
    usable = traditional.usable_rows(rows)
    if len(usable) < MIN_BARS_FOR_BACKTEST:
        raise NotEnoughBars(len(usable))

    verdicts = replay_signals(usable, rule_set)
    closes = [float(r.close) for r in usable]

    signals = [
        SignalRecord(
            date=usable[i].date,
            signal=signal,
            close=closes[i],
            reasons=reasons,
            forward=_forward_returns(closes, i),
        )
        for i, signal, reasons in verdicts
    ]

    start = MIN_SAMPLES_FOR_BFP - 1
    buy_stats = _horizon_stats(signals, "buy")
    sell_stats = _horizon_stats(signals, "sell")
    baseline = _baseline_stats(closes, start)

    return BacktestReport(
        sid=sid,
        rule_set=rule_set,
        start=usable[start].date,
        end=usable[-1].date,
        bars=len(usable),
        judged_days=len(usable) - start,
        signals=signals,
        buy_stats=buy_stats,
        sell_stats=sell_stats,
        baseline=baseline,
        edges=_edges(buy_stats, sell_stats, baseline),
        simulation=simulate(usable, verdicts, start),
    )


@dataclass(frozen=True)
class PooledHorizon:
    """One horizon, summed across every stock in a batch.

    Per-stock rates are close to unreadable on their own: a 12-month window on
    one stock yields a handful of signals, and a handful of samples produces
    rates that swing twenty points on one trade. Pooling is what turns them
    into a statement about the *rule*.

    Sums, never averages of averages. Ten stocks with one signal each and one
    stock with a hundred do not get equal votes -- `sum(wins) / sum(samples)`
    is the rate the rule actually achieved, and averaging the eleven per-stock
    rates is a different (and wrong) number.
    """

    horizon: int
    stocks: int
    buy_samples: int
    buy_win_rate: float | None
    buy_average_return: float | None
    sell_samples: int
    sell_win_rate: float | None
    sell_average_return: float | None
    baseline_samples: int
    baseline_up_rate: float | None
    baseline_average_return: float | None
    buy_edge: float | None
    sell_edge: float | None
    buy_excess_return: float | None
    sell_excess_return: float | None


def _weighted(pairs: list[tuple[float | None, int]]) -> float | None:
    """Sample-weighted mean, skipping entries that had nothing to average."""
    total = sum(n for value, n in pairs if value is not None)
    if not total:
        return None
    return sum(value * n for value, n in pairs if value is not None) / total


def pool(reports: list[BacktestReport]) -> list[PooledHorizon]:
    """Aggregate many single-stock reports into one verdict per horizon."""
    out = []
    for horizon in HORIZONS:
        buys = [next(s for s in r.buy_stats if s.horizon == horizon) for r in reports]
        sells = [next(s for s in r.sell_stats if s.horizon == horizon) for r in reports]
        bases = [next(b for b in r.baseline if b.horizon == horizon) for r in reports]

        buy_n = sum(s.samples for s in buys)
        sell_n = sum(s.samples for s in sells)
        base_n = sum(b.samples for b in bases)

        buy_rate = _rate(sum(s.wins for s in buys), buy_n)
        sell_rate = _rate(sum(s.wins for s in sells), sell_n)
        up_rate = _rate(sum(b.ups for b in bases), base_n)
        down_rate = None if up_rate is None else 1.0 - up_rate

        buy_return = _weighted([(s.average_return, s.samples) for s in buys])
        sell_return = _weighted([(s.average_return, s.samples) for s in sells])
        base_return = _weighted([(b.average_return, b.samples) for b in bases])

        out.append(
            PooledHorizon(
                horizon=horizon,
                stocks=len(reports),
                buy_samples=buy_n,
                buy_win_rate=buy_rate,
                buy_average_return=buy_return,
                sell_samples=sell_n,
                sell_win_rate=sell_rate,
                sell_average_return=sell_return,
                baseline_samples=base_n,
                baseline_up_rate=up_rate,
                baseline_average_return=base_return,
                buy_edge=_sub(buy_rate, up_rate),
                sell_edge=_sub(sell_rate, down_rate),
                buy_excess_return=_sub(buy_return, base_return),
                sell_excess_return=_sub(base_return, sell_return),
            )
        )
    return out
