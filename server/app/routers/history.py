from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import DailyPricePoint, HistoryResponse
from app.services import codes as codes_service
from app.services import history as history_service

router = APIRouter(prefix="/api/stocks", tags=["history"])


@router.get("/{sid}/history", response_model=HistoryResponse)
def get_history(
    sid: str,
    months: int = Query(6, ge=1, le=24, description="往回抓幾個月（含當月）"),
    force: bool = Query(False, description="忽略快取，強制向 TWSE/TPEX 重抓"),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    rows, fetched, cached = history_service.get_history(db, sid, months, force=force)

    return HistoryResponse(
        sid=sid,
        name=info.name,
        source=info.data_source,
        months=months,
        count=len(rows),
        fetched_months=fetched,
        cached_months=cached,
        data=[
            DailyPricePoint(
                date=r.date,
                open=float(r.open) if r.open is not None else None,
                high=float(r.high) if r.high is not None else None,
                low=float(r.low) if r.low is not None else None,
                close=float(r.close) if r.close is not None else None,
                change=float(r.change) if r.change is not None else None,
                capacity=r.capacity,
                turnover=r.turnover,
                transaction=r.transaction,
            )
            for r in rows
        ],
    )
