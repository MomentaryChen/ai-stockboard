"""Analysis endpoints.

Traditional (rule-based) analysis lives at `/analysis/traditional`. AI-assisted
analysis will be added as a sibling route so both can be requested for the same
stock and compared.

`/analysis/backtest` is what makes that comparison mean anything. Two engines
disagreeing about today settles nothing; the backtest replays the rule-based
one over bars already in the database and reports its hit rate against the base
rate of the same days -- so a second engine has a number to beat rather than an
anecdote to differ from.
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import deps
from app.db import get_db
from app.models import AppUser
from app.schemas import (
    BacktestBaselineStats,
    BacktestBatchResponse,
    BacktestEdge,
    BacktestHorizonStats,
    BacktestPooledHorizon,
    BacktestSummary,
    BestFourPointResult,
    TraditionalAnalysisBatchResponse,
    TraditionalAnalysisResponse,
    TraditionalAnalysisSummary,
)
from app.services import codes as codes_service
from app.services import history as history_service
from app.services.analysis import backtest as backtest_service
from app.services.analysis import traditional

router = APIRouter(prefix="/api/stocks", tags=["analysis"])
batch_router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# The 3/6-day MA bias pivot and the 60-day average both need history well beyond
# a single month, so we widen the window regardless of what was requested.
MIN_MONTHS = 4
# Same cap as /api/realtime. The batch path is cache-only, so this bound is
# about response size, not about how many TWSE fetches a request could queue.
MAX_BATCH = 20

# A backtest over four months judges maybe sixty days and fires a handful of
# signals; rates drawn from that read exactly as authoritative as rates drawn
# from eighty. Twelve months is the default and six the floor -- twelve is also
# ANONYMOUS_MAX_MONTHS, so the default needs no sign-in.
BACKTEST_MONTHS = 12
BACKTEST_MIN_MONTHS = 6

RuleSet = Literal["grs", "twstock"]
RuleSetParam = Annotated[
    RuleSet,
    Query(
        description=(
            "四大買賣點規則版本。grs = 修正 twstock 兩處移植缺陷後的參考行為（預設）；"
            "twstock = 套件原樣，供對照"
        ),
    ),
]

# Shown on a watchlist card when daily_price has nothing for that sid.
# Opening the stock page still fetches from the exchange (one sid, existing
# throttle); the board itself must not.
_NO_DAILY_BARS = BestFourPointResult(
    signal="hold",
    label="資料不足",
    reasons=["尚未載入日線，點進個股頁即可補齊"],
)


def _load_stock(db: Session, sid: str, months: int):
    """History + adapter, or a reason the caller should skip this sid.

    This is the single-stock path: missing months are fetched from the
    exchange, same as GET /history, one sid at a time.
    """
    info = codes_service.get_stock(sid)
    if info is None:
        return None, None, f"Stock ID '{sid}' not found"

    rows, _, _ = history_service.get_history(db, sid, max(months, MIN_MONTHS))
    stock = traditional.build_stock(rows)
    if not stock.price:
        return info, None, f"No price data available for '{sid}'"
    return info, stock, None


@router.get("/{sid}/analysis/traditional", response_model=TraditionalAnalysisResponse)
def get_traditional_analysis(
    sid: str,
    months: int = Query(6, ge=1, le=24),
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    user: AppUser | None = Depends(deps.get_optional_user),
    db: Session = Depends(get_db),
) -> TraditionalAnalysisResponse:
    # `_load_stock` backfills through the exchange, so this route spends the
    # same TWSE budget `/history` does and is metered the same way. The batch
    # sibling below needs no such check: it is cache-only by construction.
    deps.limit_anonymous_window(user, months, deps.ANONYMOUS_MAX_MONTHS, "months")

    info, stock, error = _load_stock(db, sid, months)
    if info is None or stock is None:
        raise HTTPException(status_code=404, detail=error)

    return TraditionalAnalysisResponse(
        sid=sid,
        name=info.name,
        rule_set=rule_set,
        as_of=stock.date[-1],
        sample_size=len(stock.price),
        latest_close=stock.price[-1],
        moving_averages=traditional.latest_moving_averages(stock),
        ma_series=traditional.ma_series(stock),
        best_four_point=traditional.best_four_point(stock, rule_set),
    )


@batch_router.get("/traditional", response_model=TraditionalAnalysisBatchResponse)
def get_traditional_analysis_batch(
    sids: str = Query(..., description="逗號分隔的股票代碼，例如 2330,0050"),
    months: int = Query(6, ge=1, le=24),
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    db: Session = Depends(get_db),
) -> TraditionalAnalysisBatchResponse:
    """Watchlist-sized BFP from cached daily bars only.

    Must not call the exchange. A cold 20-sid watchlist would otherwise queue
    tens of TWSE month-fetches on the same limiter the realtime poll uses
    (3 calls / 5 s). Missing cache is a 資料不足 verdict, not an upstream trip.

    Failures stay in `errors` so one unknown code does not drop the rest --
    the same contract `/api/realtime` already uses.
    """
    ids = [s.strip() for s in sids.split(",") if s.strip()][:MAX_BATCH]
    window = max(months, MIN_MONTHS)
    buckets = history_service.month_range(window)
    start = datetime.date(buckets[0][0], buckets[0][1], 1)
    rows_by_sid = history_service.read_prices_many(db, ids, start)

    items: list[TraditionalAnalysisSummary] = []
    errors: dict[str, str] = {}

    for sid in ids:
        info = codes_service.get_stock(sid)
        if info is None:
            errors[sid] = f"Stock ID '{sid}' not found"
            continue

        stock = traditional.build_stock(rows_by_sid.get(sid, []))
        if not stock.price:
            items.append(
                TraditionalAnalysisSummary(
                    sid=sid,
                    name=info.name,
                    rule_set=rule_set,
                    as_of=None,
                    sample_size=0,
                    latest_close=None,
                    best_four_point=_NO_DAILY_BARS,
                )
            )
            continue

        items.append(
            TraditionalAnalysisSummary(
                sid=sid,
                name=info.name,
                rule_set=rule_set,
                as_of=stock.date[-1],
                sample_size=len(stock.price),
                latest_close=stock.price[-1],
                best_four_point=traditional.best_four_point(stock, rule_set),
            )
        )

    return TraditionalAnalysisBatchResponse(items=items, errors=errors)


def _horizon_out(stats) -> BacktestHorizonStats:
    return BacktestHorizonStats(**vars(stats))


def _baseline_out(stats) -> BacktestBaselineStats:
    return BacktestBaselineStats(**vars(stats))


def _edge_out(edge) -> BacktestEdge:
    return BacktestEdge(**vars(edge))


def _unscored(sid: str, name: str, rule_set: str, note: str) -> BacktestSummary:
    return BacktestSummary(
        sid=sid,
        name=name,
        rule_set=rule_set,
        start=None,
        end=None,
        bars=0,
        judged_days=0,
        signal_count=0,
        buy_stats=[],
        sell_stats=[],
        baseline=[],
        edges=[],
        strategy_return=None,
        buy_hold_return=None,
        exposure=None,
        trade_count=0,
        note=note,
    )


@batch_router.get("/backtest", response_model=BacktestBatchResponse)
def get_backtest_batch(
    sids: str = Query(..., description="逗號分隔的股票代碼，例如 2330,0050"),
    months: int = Query(BACKTEST_MONTHS, ge=BACKTEST_MIN_MONTHS, le=24),
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    db: Session = Depends(get_db),
) -> BacktestBatchResponse:
    """Score a basket of stocks and pool the result.

    `pooled` is the reason this route exists. Per-stock rates over a year are a
    handful of samples each and say more about that stock's year than about the
    rule; summed across a basket they become a statement about the rule --
    specifically `pooled[].buy_edge`, which is the hit rate minus the base rate
    of the same days, and is the number that decides whether 四大買賣點 is worth
    using as a benchmark for anything else.

    Cache-only, for the same reason the traditional batch is: twenty cold sids
    would otherwise queue tens of month-fetches on the limiter the realtime
    poll shares. Missing history is a note on the item, not an upstream trip.
    """
    ids = [s.strip() for s in sids.split(",") if s.strip()][:MAX_BATCH]
    buckets = history_service.month_range(months)
    start = datetime.date(buckets[0][0], buckets[0][1], 1)
    rows_by_sid = history_service.read_prices_many(db, ids, start)

    items: list[BacktestSummary] = []
    errors: dict[str, str] = {}
    reports = []

    for sid in ids:
        info = codes_service.get_stock(sid)
        if info is None:
            errors[sid] = f"Stock ID '{sid}' not found"
            continue

        try:
            report = backtest_service.run(sid, rows_by_sid.get(sid, []), rule_set)
        except backtest_service.NotEnoughBars as exc:
            items.append(_unscored(sid, info.name, rule_set, str(exc)))
            continue

        reports.append(report)
        items.append(
            BacktestSummary(
                sid=sid,
                name=info.name,
                rule_set=rule_set,
                start=report.start,
                end=report.end,
                bars=report.bars,
                judged_days=report.judged_days,
                signal_count=len(report.signals),
                buy_stats=[_horizon_out(s) for s in report.buy_stats],
                sell_stats=[_horizon_out(s) for s in report.sell_stats],
                baseline=[_baseline_out(b) for b in report.baseline],
                edges=[_edge_out(e) for e in report.edges],
                strategy_return=report.simulation.strategy_return,
                buy_hold_return=report.simulation.buy_hold_return,
                exposure=report.simulation.exposure,
                trade_count=len(report.simulation.trades),
                note=None,
            )
        )

    return BacktestBatchResponse(
        items=items,
        pooled=[
            BacktestPooledHorizon(**vars(p)) for p in backtest_service.pool(reports)
        ]
        if reports
        else [],
        errors=errors,
    )
