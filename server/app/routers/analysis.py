"""Analysis endpoints.

Three sibling routes answer questions about the same stock, shaped differently
on purpose.

`/analysis/traditional` is the rule engine: free, deterministic, and a GET.

`/analysis/backtest` is what makes a comparison between engines mean anything.
Two engines disagreeing about today settles nothing; the backtest replays the
rule-based one over bars already in the database and reports its hit rate
against the base rate of the same days -- so a second engine has a number to
beat rather than an anecdote to differ from.

`/analysis/ai` is that second engine. It costs a Gemini request, so it answers
on POST, requires a sign-in, and serves a shared cache keyed on the trading day
-- see `services/analysis/ai.py` for what each of those is defending. It has no
backtest of its own yet, which is the honest gap between it and the route above.
"""

import datetime
import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import deps
from app.db import get_db
from app.models import AppUser
from app.schemas import (
    AiAnalysisResponse,
    AiQuotaStatus,
    BacktestBaselineStats,
    BacktestBatchResponse,
    BacktestEdge,
    BacktestHorizonStats,
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
from app.services.analysis import ai as ai_service
from app.services.analysis import backtest as backtest_service
from app.services.analysis import gemini, traditional

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


@router.get("/{sid}/analysis/backtest", response_model=BacktestResponse)
def get_backtest(
    sid: str,
    rule_set: RuleSetParam = traditional.DEFAULT_RULE_SET,
    db: Session = Depends(get_db),
) -> BacktestResponse:
    """How the 四大買賣點 verdict has actually performed, for one stock.

    Answers the same question as `/api/analysis/backtest` and differs from it
    on one axis, which is what makes each of them cheap in its own way:

      * That route takes a `months` window and scores a basket fresh every
        time. A caller-chosen window cannot be cached, and pooling is the point
        there, so it does not try.
      * This one is pinned to `BACKTEST_WINDOW_MONTHS` and reads
        `backtest_result`. It backs a card on a page that anyone can load, so
        it has to be a lookup rather than a 240-day replay per view -- and a
        fixed window is what makes a stored row answerable at all.

    Unmetered because it is cache-only: it replays bars already in
    `daily_price` and never calls the exchange, so there is no upstream budget
    to spend. A stock nobody has loaded history for has nothing to replay and
    gets a 422 rather than a fabricated verdict; opening the stock page is what
    fills `daily_price` for it.
    """
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    try:
        return backtest_store.get_or_compute(db, sid, info.name, rule_set)
    except backtest_service.NotEnoughBars as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


# --- AI analysis -------------------------------------------------------------

#: Fixed, and deliberately not a query parameter.
#:
#: The stored verdict is keyed on (sid, trading day, model, prompt, locale). If
#: the caller could choose the history window, two requests for the same day
#: would build different features, and whichever arrived first would decide what
#: everyone else reads for that day. Widening the window is a prompt-version
#: change, because it changes what the model saw.
AI_MONTHS = 6


@router.post("/{sid}/analysis/ai", response_model=AiAnalysisResponse)
def generate_ai_analysis(
    sid: str,
    response: Response,
    locale: str = Query(
        ai_service.DEFAULT_LOCALE, description="判讀要用哪個語言生成（zh-TW / en）"
    ),
    regenerate: bool = Depends(deps.regenerate_ai),
    user: AppUser = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
) -> AiAnalysisResponse:
    """A position call for one stock: enter/exit/hold, and at what size.

    POST rather than GET because a miss spends money and writes a row. A hit
    spends neither, which is what lets the button live on every watchlist card.
    """
    if not gemini.is_configured():
        raise HTTPException(
            status_code=503, detail="AI analysis is not configured on this server"
        )

    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    rows, _, _ = history_service.get_history(db, sid, AI_MONTHS)

    try:
        result = ai_service.get_or_create(
            db,
            sid=sid,
            name=info.name,
            rows=rows,
            user=user,
            locale=locale,
            force=regenerate,
        )
    except ai_service.InsufficientData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ai_service.QuotaExceeded as exc:
        # Retry-After in seconds, so a client can say when rather than just that.
        wait = (exc.status.resets_at - datetime.datetime.now(datetime.timezone.utc))
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(max(1, math.ceil(wait.total_seconds())))},
        ) from exc
    except gemini.AiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except gemini.AiFailed as exc:
        # 502, not 500: this service worked, its upstream did not -- the same
        # distinction the realtime path draws when TWSE MIS refuses.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Lets the client show "已快取" without inspecting the body, and keeps a
    # proxy from ever storing a metered response as if it were free.
    response.headers["Cache-Control"] = "no-store"
    return result


@batch_router.get("/ai/quota", response_model=AiQuotaStatus)
def get_ai_quota(
    user: AppUser = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
) -> AiQuotaStatus:
    """What is left of this account's daily allowance.

    Read before the button is pressed, so the UI can disable it with a reason
    instead of letting the request come back 429.
    """
    return ai_service.quota_status(db, user)


# --- Backtest ----------------------------------------------------------------


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
