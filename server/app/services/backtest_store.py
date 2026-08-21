"""Persisting the signal replay, and deciding when it has to be redone.

`services/analysis/backtest.py` is pure -- bars in, report out. This module is
everything around it: which stocks are worth replaying, when a stored row has
been overtaken by newer bars, and turning a report into the row and the
response.

The division of labour with the nightly job is deliberate:

  * The job is a **warmer**, not the source of truth. It walks the stocks we
    already hold bars for and stores a row for each.
  * The endpoint is **self-healing**. A stock nobody has warmed, or one that
    traded since the last run, is recomputed on the spot and stored on the way
    out. So a cold cache is a slower first answer, never a wrong one or a
    missing card -- which matters because the job only ever knows about the
    stocks somebody has already looked at.

Staleness is measured against the newest bar in `daily_price`, not against a
clock. A stock that has not traded since the last run does not need recomputing
however long ago that was, and one that has does, however recently the job
happened to fire.

Like `history.py`, the response is always read back out of PostgreSQL rather
than returned straight from the object that was just computed, so a cached and
a freshly-computed answer cannot drift into different shapes.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BacktestResult, DailyPrice
from app.schemas import (
    BacktestBaselineStats,
    BacktestEdge,
    BacktestEquityPoint,
    BacktestHorizonStats,
    BacktestResponse,
    BacktestSignalOut,
    BacktestSimulationOut,
    BacktestTradeOut,
)
from app.services import history as history_service
from app.services.analysis import backtest, traditional

logger = logging.getLogger(__name__)
settings = get_settings()

#: How many of the most recent signals travel in the response. The whole list
#: can run to eighty entries and nothing reads past the newest handful; the
#: aggregate stats above them already account for every one.
MAX_SIGNALS_RETURNED = 12


def window_start(today: datetime.date | None = None) -> datetime.date:
    """First day of the trailing window, month-aligned like `/history` is."""
    buckets = history_service.month_range(settings.backtest_window_months, today)
    return datetime.date(buckets[0][0], buckets[0][1], 1)


def _to_row(report: backtest.BacktestReport) -> dict:
    """Flatten a report into the column + JSONB payload split.

    Headline figures become columns so "where does this signal actually work"
    is an ORDER BY; the curve and the lists are JSONB because nothing queries
    into them and they are only ever rendered whole.
    """
    sim = report.simulation
    wins = sum(1 for t in sim.trades if t.profit > 0)
    holding = [t.holding_days for t in sim.trades]

    return {
        "sid": report.sid,
        "rule_set": report.rule_set,
        "window_months": settings.backtest_window_months,
        "start_date": report.start,
        "end_date": report.end,
        "bars": report.bars,
        "judged_days": report.judged_days,
        "buy_signals": sum(1 for s in report.signals if s.signal == "buy"),
        "sell_signals": sum(1 for s in report.signals if s.signal == "sell"),
        "trades": len(sim.trades),
        "trade_wins": wins,
        "strategy_return": sim.strategy_return,
        "buy_hold_return": sim.buy_hold_return,
        "max_drawdown": sim.max_drawdown,
        "buy_hold_max_drawdown": sim.buy_hold_max_drawdown,
        "exposure": sim.exposure,
        "computed_through": report.end,
        "computed_at": datetime.datetime.now(datetime.timezone.utc),
        "payload": {
            "buy": [_horizon_json(h) for h in report.buy_stats],
            "sell": [_horizon_json(h) for h in report.sell_stats],
            "baseline": [
                {
                    "horizon": b.horizon,
                    "samples": b.samples,
                    "ups": round((b.up_rate or 0.0) * b.samples),
                    "up_rate": b.up_rate,
                    "average_return": b.average_return,
                    "median_return": b.median_return,
                }
                for b in report.baseline
            ],
            "edges": [
                {
                    "horizon": e.horizon,
                    "buy_edge": e.buy_edge,
                    "sell_edge": e.sell_edge,
                    "buy_excess_return": e.buy_excess_return,
                    "sell_excess_return": e.sell_excess_return,
                }
                for e in report.edges
            ],
            "average_holding_days": (sum(holding) / len(holding)) if holding else None,
            "open_position": (
                {
                    "entry_date": sim.open_entry[0].isoformat(),
                    "entry_price": sim.open_entry[1],
                }
                if sim.open_entry
                else None
            ),
            "equity": [
                {"date": day.isoformat(), "strategy": strategy, "buy_hold": bh}
                for day, strategy, bh in sim.equity
            ],
            "trades": [
                {
                    "entry_date": t.entry_date.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date.isoformat(),
                    "exit_price": t.exit_price,
                    "holding_days": t.holding_days,
                    "profit": t.profit,
                }
                for t in sim.trades
            ],
            # Newest first: the card shows the tail of the list, and reversing
            # here means the cap below keeps the recent ones rather than the
            # oldest ones.
            "signals": [
                {
                    "date": s.date.isoformat(),
                    "signal": s.signal,
                    "close": s.close,
                    "reasons": s.reasons,
                    "forward": {str(h): v for h, v in sorted(s.forward.items())},
                }
                for s in reversed(report.signals)
            ],
        },
    }


def _horizon_json(stats: backtest.HorizonStats) -> dict:
    return {
        "horizon": stats.horizon,
        "samples": stats.samples,
        "wins": stats.wins,
        "pending": stats.pending,
        "win_rate": stats.win_rate,
        "average_return": stats.average_return,
        "median_return": stats.median_return,
    }


def _upsert(db: Session, row: dict) -> None:
    stmt = pg_insert(BacktestResult).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=[BacktestResult.sid, BacktestResult.rule_set],
        set_={key: stmt.excluded[key] for key in row if key not in ("sid", "rule_set")},
    )
    db.execute(stmt)
    db.commit()


def latest_bar(db: Session, sid: str) -> datetime.date | None:
    return db.execute(
        select(func.max(DailyPrice.date)).where(DailyPrice.sid == sid)
    ).scalar_one_or_none()


def read(db: Session, sid: str, rule_set: str) -> BacktestResult | None:
    return db.execute(
        select(BacktestResult).where(
            BacktestResult.sid == sid, BacktestResult.rule_set == rule_set
        )
    ).scalar_one_or_none()


def simulation_of(row: BacktestResult, *, with_equity: bool = True) -> BacktestSimulationOut:
    """The stored simulation. `with_equity=False` for batch callers.

    The curve is ~240 points per stock; a twenty-stock batch that carried them
    all would ship a megabyte to draw nothing, because the batch reports rates
    rather than plotting anything.
    """
    payload = row.payload or {}
    open_position = payload.get("open_position") or {}

    return BacktestSimulationOut(
        trades=[BacktestTradeOut(**trade) for trade in payload.get("trades", [])],
        trade_count=row.trades,
        winning_trades=row.trade_wins,
        strategy_return=float(row.strategy_return or 0.0),
        buy_hold_return=float(row.buy_hold_return or 0.0),
        max_drawdown=float(row.max_drawdown or 0.0),
        buy_hold_max_drawdown=float(row.buy_hold_max_drawdown or 0.0),
        open_entry_date=open_position.get("entry_date"),
        open_entry_price=open_position.get("entry_price"),
        exposure=float(row.exposure or 0.0),
        # Null rather than zero when nothing closed: "never won" and "never
        # traded" are different answers and must not render the same.
        trade_win_rate=(row.trade_wins / row.trades) if row.trades else None,
        average_holding_days=payload.get("average_holding_days"),
        equity=(
            [BacktestEquityPoint(**point) for point in payload.get("equity", [])]
            if with_equity
            else []
        ),
    )


def horizons_of(row: BacktestResult, key: str) -> list[BacktestHorizonStats]:
    return [BacktestHorizonStats(**h) for h in (row.payload or {}).get(key, [])]


def baseline_of(row: BacktestResult) -> list[BacktestBaselineStats]:
    return [BacktestBaselineStats(**b) for b in (row.payload or {}).get("baseline", [])]


def edges_of(row: BacktestResult) -> list[BacktestEdge]:
    return [BacktestEdge(**e) for e in (row.payload or {}).get("edges", [])]


def _to_response(row: BacktestResult, name: str, cached: bool) -> BacktestResponse:
    payload = row.payload or {}

    return BacktestResponse(
        sid=row.sid,
        name=name,
        rule_set=row.rule_set,
        window_months=row.window_months,
        start=row.start_date,
        end=row.end_date,
        bars=row.bars,
        judged_days=row.judged_days,
        signal_count=row.buy_signals + row.sell_signals,
        buy_stats=horizons_of(row, "buy"),
        sell_stats=horizons_of(row, "sell"),
        baseline=baseline_of(row),
        edges=edges_of(row),
        simulation=simulation_of(row),
        signals=[
            BacktestSignalOut(**signal)
            for signal in payload.get("signals", [])[:MAX_SIGNALS_RETURNED]
        ],
        computed_at=row.computed_at,
        computed_through=row.computed_through,
        cached=cached,
    )


def compute(db: Session, sid: str, rule_set: str) -> BacktestResult:
    """Replay `sid` over the trailing window and store the result.

    Cache-only on the way in: reads whatever bars are already landed and never
    reaches for the exchange. A backtest that triggered a year of month-fetches
    would put a page load behind ~12 throttled upstream calls, and the caller
    that actually wants that history (`/history`, `/analysis/traditional`) has
    already fetched it by the time this runs.

    Raises `backtest.NotEnoughBars` when there is too little history.
    """
    rows = history_service.read_prices(db, sid, window_start())
    report = backtest.run(sid, rows, rule_set)
    _upsert(db, _to_row(report))

    stored = read(db, sid, rule_set)
    if stored is None:  # pragma: no cover -- upsert just wrote it
        raise RuntimeError(f"backtest row for {sid}/{rule_set} vanished after upsert")
    return stored


def get_or_compute(
    db: Session, sid: str, name: str, rule_set: str = traditional.DEFAULT_RULE_SET
) -> BacktestResponse:
    """The stored replay, recomputed first if the bars have moved past it."""
    row = read(db, sid, rule_set)
    newest = latest_bar(db, sid)

    fresh = (
        row is not None
        and newest is not None
        and row.computed_through >= newest
        and row.window_months == settings.backtest_window_months
    )
    if fresh:
        return _to_response(row, name, cached=True)

    return _to_response(compute(db, sid, rule_set), name, cached=False)


def read_many(
    db: Session, sids: list[str], rule_set: str
) -> dict[str, BacktestResult]:
    """Stored replays for many sids, one query. For the batch path."""
    if not sids:
        return {}
    stmt = select(BacktestResult).where(
        BacktestResult.sid.in_(sids), BacktestResult.rule_set == rule_set
    )
    return {row.sid: row for row in db.execute(stmt).scalars()}


def _weighted(pairs: list[tuple[float | None, int]]) -> float | None:
    """Mean of per-stock averages weighted by how many samples each rests on.

    An unweighted mean would let a stock with three signals move the pooled
    figure as much as one with eighty, which is precisely the distortion
    pooling exists to remove.
    """
    usable = [(value, weight) for value, weight in pairs if value is not None and weight]
    if not usable:
        return None
    total = sum(weight for _, weight in usable)
    return sum(value * weight for value, weight in usable) / total if total else None


def pool(rows: list[BacktestResult]) -> list[dict]:
    """Sum the per-stock horizon stats into one readable set of rates.

    Counts are summed and the rate recomputed from the totals -- not averaged
    from per-stock rates, which would weight a three-signal stock like an
    eighty-signal one. The sample counts travel with the rates because a pooled
    figure over thirty signals is still noise, and the reader has to be able to
    see that.
    """
    pooled = []
    for horizon in backtest.HORIZONS:
        buy = _pick(rows, "buy", horizon)
        sell = _pick(rows, "sell", horizon)
        base = _pick(rows, "baseline", horizon)

        buy_samples = sum(s["samples"] for s in buy)
        sell_samples = sum(s["samples"] for s in sell)
        base_samples = sum(s["samples"] for s in base)

        buy_rate = _rate(sum(s["wins"] for s in buy), buy_samples)
        sell_rate = _rate(sum(s["wins"] for s in sell), sell_samples)
        up_rate = _rate(sum(s.get("ups", 0) for s in base), base_samples)
        down_rate = None if up_rate is None else 1.0 - up_rate

        buy_avg = _weighted([(s["average_return"], s["samples"]) for s in buy])
        sell_avg = _weighted([(s["average_return"], s["samples"]) for s in sell])
        base_avg = _weighted([(s["average_return"], s["samples"]) for s in base])

        pooled.append(
            {
                "horizon": horizon,
                "stocks": len(rows),
                "buy_samples": buy_samples,
                "buy_win_rate": buy_rate,
                "buy_average_return": buy_avg,
                "sell_samples": sell_samples,
                "sell_win_rate": sell_rate,
                "sell_average_return": sell_avg,
                "baseline_samples": base_samples,
                "baseline_up_rate": up_rate,
                "baseline_average_return": base_avg,
                "buy_edge": _diff(buy_rate, up_rate),
                "sell_edge": _diff(sell_rate, down_rate),
                "buy_excess_return": _diff(buy_avg, base_avg),
                # What getting out avoided, so the subtraction runs the other
                # way -- the same trap `Edge` exists to keep callers out of.
                "sell_excess_return": _diff(base_avg, sell_avg),
            }
        )
    return pooled


def _pick(rows: list[BacktestResult], key: str, horizon: int) -> list[dict]:
    out = []
    for row in rows:
        for entry in (row.payload or {}).get(key, []):
            if entry.get("horizon") == horizon:
                out.append(entry)
    return out


def _rate(wins: int, samples: int) -> float | None:
    return wins / samples if samples else None


def _diff(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def backtestable_sids(db: Session) -> list[str]:
    """Stocks with enough landed bars in the window to be worth replaying.

    Scoped to what `daily_price` already holds rather than to the 44k-row
    listing: the roster is what the exchange lists, and this is what we have
    history for. The two differ by four orders of magnitude, and closing the
    gap would mean scraping -- which is exactly what this feature was supposed
    to avoid needing.
    """
    stmt = (
        select(DailyPrice.sid)
        .where(DailyPrice.date >= window_start(), DailyPrice.close.is_not(None))
        .group_by(DailyPrice.sid)
        .having(func.count() >= backtest.MIN_BARS_FOR_BACKTEST)
        .order_by(DailyPrice.sid)
    )
    return list(db.execute(stmt).scalars())


def refresh_all(db: Session, force: bool = False) -> dict[str, int]:
    """Warm every (stock, rule set) pair whose bars have moved. For the job.

    Both rule sets, because the card lets the reader switch between them and a
    warm 修正版 next to a cold twstock would make the comparison feel like a
    performance difference rather than a rules difference.

    One stock failing does not stop the rest: a replay is derived data, and the
    endpoint recomputes anything this skipped anyway.
    """
    computed = skipped = failed = 0

    for sid in backtestable_sids(db):
        newest = latest_bar(db, sid)
        for rule_set in traditional.RULE_SETS:
            row = read(db, sid, rule_set)
            current = (
                not force
                and row is not None
                and newest is not None
                and row.computed_through >= newest
                and row.window_months == settings.backtest_window_months
            )
            if current:
                skipped += 1
                continue
            try:
                compute(db, sid, rule_set)
                computed += 1
            except backtest.NotEnoughBars:
                # Bars in the window but not enough of them usable -- the
                # `having` count above cannot see the non-trading days that
                # `usable_rows` drops.
                skipped += 1
            except Exception:
                logger.warning(
                    "backtest refresh failed sid=%s rule_set=%s", sid, rule_set,
                    exc_info=True,
                )
                db.rollback()
                failed += 1

    return {"computed": computed, "skipped": skipped, "failed": failed}
