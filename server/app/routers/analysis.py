"""Analysis endpoints.

Traditional (rule-based) analysis lives at `/analysis/traditional`; the AI
engine sits beside it at `/analysis/ai`, so both can be requested for the same
stock and put next to each other.

The two are shaped differently on purpose. The rule engine is free, deterministic
and answers on GET. The AI engine costs a Gemini request, so it answers on POST,
requires a sign-in, and serves a shared cache keyed on the trading day -- see
`services/analysis/ai.py` for what each of those is defending.
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
    BestFourPointResult,
    TraditionalAnalysisBatchResponse,
    TraditionalAnalysisResponse,
    TraditionalAnalysisSummary,
)
from app.services import codes as codes_service
from app.services import history as history_service
from app.services.analysis import ai as ai_service
from app.services.analysis import gemini, traditional

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
