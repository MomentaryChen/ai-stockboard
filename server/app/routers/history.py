"""歷史日成交 (daily bars), served database-first.

Public, because the answer comes out of PostgreSQL. A cache miss still reaches
TWSE/TPEX though, so the two knobs that decide *how much* it may fetch --
`force` and `months` -- are metered by `app.deps`: forcing needs an ADMIN, and
an anonymous caller gets the range the public chart offers rather than the
widest the API accepts. Without that, this route is a way around the sign-in
that protects `/api/realtime`'s share of the same rate limit.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import deps
from app.db import get_db
from app.models import AppUser
from app.schemas import DailyPricePoint, HistoryResponse
from app.services import codes as codes_service
from app.services import history as history_service

router = APIRouter(prefix="/api/stocks", tags=["history"])


@router.get("/{sid}/history", response_model=HistoryResponse)
def get_history(
    sid: str,
    months: int = Query(6, ge=1, le=24, description="往回抓幾個月（含當月）"),
    force: bool = Depends(deps.force_refresh),
    user: AppUser | None = Depends(deps.get_optional_user),
    db: Session = Depends(get_db),
) -> HistoryResponse:
    # Before the 404: a cold sid costs one upstream request per month in the
    # range, so the range has to be allowed before we look anything up.
    deps.limit_anonymous_window(user, months, deps.ANONYMOUS_MAX_MONTHS, "months")

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
