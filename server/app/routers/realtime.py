"""即時報價 from the TWSE MIS endpoint.

The only market-data route behind a sign-in. History, search and the rule
analysis stay public because they answer out of the PostgreSQL cache, but a
quote is worthless unless it is fresh, so every call reaches upstream and spends
part of a budget the whole service shares -- TWSE allows 3 requests per 5
seconds per source IP, and each open board burns one every 10 seconds. Requiring
an account is what keeps that budget attributable.
"""

from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user
from app.models import AppUser
from app.schemas import RealtimeResponse
from app.services import realtime as realtime_service

router = APIRouter(prefix="/api", tags=["realtime"])


@router.get("/realtime", response_model=RealtimeResponse)
def get_realtime(
    sids: str = Query(..., description="逗號分隔的股票代碼，例如 2330,0050"),
    # Bound but unused: the identity does not change the answer, it only decides
    # whether there is one. 401 on a missing or expired token, so the frontend
    # interceptor still gets its chance to refresh mid-poll.
    _user: AppUser = Depends(get_current_user),
) -> RealtimeResponse:
    ids = [s.strip() for s in sids.split(",") if s.strip()][:20]
    return realtime_service.get_quotes(ids)
