"""Analysis endpoints.

Traditional (rule-based) analysis lives at `/analysis/traditional`. AI-assisted
analysis will be added as a sibling route so both can be requested for the same
stock and compared.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import TraditionalAnalysisResponse
from app.services import codes as codes_service
from app.services import history as history_service
from app.services.analysis import traditional

router = APIRouter(prefix="/api/stocks", tags=["analysis"])

# The 3/6-day MA bias pivot and the 60-day average both need history well beyond
# a single month, so we widen the window regardless of what was requested.
MIN_MONTHS = 4


@router.get("/{sid}/analysis/traditional", response_model=TraditionalAnalysisResponse)
def get_traditional_analysis(
    sid: str,
    months: int = Query(6, ge=1, le=24),
    rule_set: Literal["grs", "twstock"] = Query(
        traditional.DEFAULT_RULE_SET,
        description=(
            "四大買賣點規則版本。grs = 修正 twstock 兩處移植缺陷後的參考行為（預設）；"
            "twstock = 套件原樣，供對照"
        ),
    ),
    db: Session = Depends(get_db),
) -> TraditionalAnalysisResponse:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    rows, _, _ = history_service.get_history(db, sid, max(months, MIN_MONTHS))
    stock = traditional.build_stock(rows)

    if not stock.price:
        raise HTTPException(
            status_code=404,
            detail=f"No price data available for '{sid}'",
        )

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
