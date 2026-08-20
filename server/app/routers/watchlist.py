"""The signed-in user's 自選股.

Writes replace the whole list rather than patching single entries: the frontend
holds an ordered `string[]` and this maps onto it one-to-one, carries the
ordering for free, and leaves no room for two concurrent edits to interleave
into a list neither client asked for.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import AppUser
from app.schemas import WatchlistResponse, WatchlistUpdateRequest
from app.services import watchlist as watchlist_service

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=WatchlistResponse)
def get_watchlist(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    sids = watchlist_service.list_sids(db, user.id)
    return WatchlistResponse(count=len(sids), sids=sids)


@router.put("", response_model=WatchlistResponse)
def replace_watchlist(
    payload: WatchlistUpdateRequest,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    try:
        sids = watchlist_service.replace(db, user.id, payload.sids)
    except watchlist_service.UnknownStockError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except watchlist_service.TooManyItemsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Returns the resulting list so the client can setQueryData without a
    # follow-up GET.
    return WatchlistResponse(count=len(sids), sids=sids)
