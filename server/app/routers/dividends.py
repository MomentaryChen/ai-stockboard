from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import DividendEventOut, DividendResponse
from app.services import codes as codes_service
from app.services import dividend as dividend_service

router = APIRouter(prefix="/api/stocks", tags=["dividends"])


def _as_float(value) -> float | None:
    return float(value) if value is not None else None


@router.get("/{sid}/dividends", response_model=DividendResponse)
def get_dividends(
    sid: str,
    years: int = Query(5, ge=1, le=10, description="往回抓幾年（含今年）"),
    force: bool = Query(False, description="忽略快取，強制向 TWSE/TPEX 重抓"),
    db: Session = Depends(get_db),
) -> DividendResponse:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    events, coverage, latest_close = dividend_service.get_dividends(
        db, sid, years, force=force
    )

    today = date.today()
    cutoff = today - timedelta(days=365)
    ttm_cash = sum(
        float(row.cash_dividend)
        for row in events
        if row.cash_dividend is not None and cutoff < row.ex_date <= today
    )
    yield_percent = None
    if coverage == "history" and latest_close and latest_close > 0:
        yield_percent = round(ttm_cash / latest_close * 100, 2)

    return DividendResponse(
        sid=sid,
        name=info.name,
        source=info.data_source,
        coverage=coverage,
        years=years,
        count=len(events),
        ttm_cash=round(ttm_cash, 4) if events or coverage == "history" else None,
        latest_close=latest_close,
        yield_percent=yield_percent,
        events=[
            DividendEventOut(
                ex_date=row.ex_date,
                kind=row.kind,
                cash_dividend=_as_float(row.cash_dividend),
                stock_dividend=_as_float(row.stock_dividend),
                deduction=_as_float(row.deduction),
                close_before=_as_float(row.close_before),
                reference_price=_as_float(row.reference_price),
                upcoming=row.ex_date > today,
            )
            for row in events
        ],
    )
