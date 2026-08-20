from fastapi import APIRouter, Query

from app.schemas import RealtimeResponse
from app.services import realtime as realtime_service

router = APIRouter(prefix="/api", tags=["realtime"])


@router.get("/realtime", response_model=RealtimeResponse)
def get_realtime(
    sids: str = Query(..., description="逗號分隔的股票代碼，例如 2330,0050"),
) -> RealtimeResponse:
    ids = [s.strip() for s in sids.split(",") if s.strip()][:20]
    return realtime_service.get_quotes(ids)
