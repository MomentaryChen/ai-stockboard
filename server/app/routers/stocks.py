from fastapi import APIRouter, HTTPException, Query

from app.schemas import SearchResponse, StockInfo
from app.services import codes as codes_service

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# Declared before /{sid} so "search" is not swallowed by the path parameter.
@router.get("/search", response_model=SearchResponse)
def search_stocks(
    q: str = Query(..., min_length=1, description="股票代碼或名稱關鍵字"),
    limit: int = Query(30, ge=1, le=100),
) -> SearchResponse:
    total, results = codes_service.search(q, limit)
    return SearchResponse(query=q, total=total, results=results)


@router.get("/{sid}", response_model=StockInfo)
def get_stock(sid: str) -> StockInfo:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")
    return info
