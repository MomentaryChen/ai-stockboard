"""Analysis endpoints.

Traditional (rule-based) analysis lives at `/analysis/traditional`. AI-assisted
analysis will be added as a sibling route so both can be requested for the same
stock and compared.
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import deps
from app.db import get_db
from app.models import AppUser
from app.schemas import (
    BacktestBatchResponse,
    BacktestPooledHorizon,
    BacktestResponse,
    BacktestSummary,
    BestFourPointResult,
    TraditionalAnalysisBatchResponse,
    TraditionalAnalysisResponse,
    TraditionalAnalysisSummary,
)
from app.services import backtest_store
from app.services import codes as codes_service
from app.services import history as history_service
from app.services.analysis import backtest, traditional

router = APIRouter(prefix="/api/stocks", tags=["analysis"])
batch_router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# The 3/6-day MA bias pivot and the 60-day average both need history well beyond
# a single month, so we widen the window regardless of what was requested.
MIN_MONTHS = 4
# Same cap as /api/realtime. The batch path is cache-only, so this bound is
# about response size, not about how many TWSE fetches a request could queue.
MAX_BATCH = 20

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


@router.get("/{sid}/analysis/backtest", response_model=BacktestResponse)
def get_backtest(
    sid: str,
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    db: Session = Depends(get_db),
) -> BacktestResponse:
    """How the 四大買賣點 verdict has actually performed over the past year.

    Deliberately unmetered and with no `months` parameter, unlike its siblings
    above. Both follow from it being cache-only: it replays the bars already in
    `daily_price` and never calls the exchange, so there is no upstream budget
    to spend -- and the window is fixed because a caller-chosen short one would
    mostly answer with win rates drawn from two or three signals.

    A stock nobody has loaded history for yet has no bars to replay and gets a
    422 rather than a fabricated verdict; opening the stock page first is what
    fills the table.
    """
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    try:
        return backtest_store.get_or_compute(db, sid, info.name, rule_set)
    except backtest.NotEnoughBars as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@batch_router.get("/backtest", response_model=BacktestBatchResponse)
def get_backtest_batch(
    sids: str = Query(..., description="逗號分隔的股票代碼，例如 2330,0050"),
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    db: Session = Depends(get_db),
) -> BacktestBatchResponse:
    """Several stocks' replays, plus the pooled rates across them.

    `pooled` is the number worth reading. One stock over one year produces a
    handful of signals per horizon, and a rate off four samples moves twenty
    points on a single trade; summing the counts across stocks first is what
    makes the answer say something about the *rule* rather than about one
    company's year.

    Cache-only and self-healing per sid, like the single-stock route: a stock
    with no stored row is computed from bars already held, and one with too few
    bars comes back with nulls and a `note` rather than being dropped -- a
    stock the pooled figure does not cover must not silently disappear from a
    list that claims to.
    """
    ids = [s.strip() for s in sids.split(",") if s.strip()][:MAX_BATCH]
    stored = backtest_store.read_many(db, ids, rule_set)

    items: list[BacktestSummary] = []
    errors: dict[str, str] = {}
    poolable = []

    for sid in ids:
        info = codes_service.get_stock(sid)
        if info is None:
            errors[sid] = f"Stock ID '{sid}' not found"
            continue

        row = stored.get(sid)
        newest = backtest_store.latest_bar(db, sid)
        if row is None or newest is None or row.computed_through < newest:
            try:
                row = backtest_store.compute(db, sid, rule_set)
            except backtest.NotEnoughBars as exc:
                items.append(_empty_summary(sid, info.name, rule_set, str(exc)))
                continue

        poolable.append(row)
        items.append(
            BacktestSummary(
                sid=sid,
                name=info.name,
                rule_set=rule_set,
                start=row.start_date,
                end=row.end_date,
                bars=row.bars,
                judged_days=row.judged_days,
                signal_count=row.buy_signals + row.sell_signals,
                buy_stats=backtest_store.horizons_of(row, "buy"),
                sell_stats=backtest_store.horizons_of(row, "sell"),
                baseline=backtest_store.baseline_of(row),
                edges=backtest_store.edges_of(row),
                strategy_return=float(row.strategy_return or 0.0),
                buy_hold_return=float(row.buy_hold_return or 0.0),
                exposure=float(row.exposure or 0.0),
                trade_count=row.trades,
                note=None,
            )
        )

    return BacktestBatchResponse(
        items=items,
        pooled=[BacktestPooledHorizon(**p) for p in backtest_store.pool(poolable)],
        errors=errors,
    )


def _empty_summary(
    sid: str, name: str, rule_set: str, note: str
) -> BacktestSummary:
    """A stock the cache could not score. Listed with its reason, not dropped."""
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
