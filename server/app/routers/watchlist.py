"""The signed-in user's 自選股.

Writes replace the whole list rather than patching single entries: the frontend
holds an ordered `string[]` and this maps onto it one-to-one, carries the
ordering for free, and leaves no room for two concurrent edits to interleave
into a list neither client asked for.

Groups ride along on the same response so the board can render folders without
a second round trip. Creating, renaming and deleting a group are their own
routes because they are not a sid-list edit; every one of them still returns
the full snapshot, matching PUT /api/watchlist.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import AppUser
from app.schemas import (
    WatchlistGroupCreateRequest,
    WatchlistGroupOut,
    WatchlistGroupUpdateRequest,
    WatchlistResponse,
    WatchlistUpdateRequest,
)
from app.services import watchlist as watchlist_service
from app.services.watchlist import WatchlistState

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _response(state: WatchlistState) -> WatchlistResponse:
    return WatchlistResponse(
        count=len(state.sids),
        sids=list(state.sids),
        groups=[
            WatchlistGroupOut(id=group.id, name=group.name, position=group.position)
            for group in state.groups
        ],
        group_by_sid=state.group_by_sid,
    )


def _http(exc: watchlist_service.WatchlistError) -> HTTPException:
    if isinstance(
        exc,
        (
            watchlist_service.UnknownStockError,
            watchlist_service.UnknownGroupError,
        ),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=WatchlistResponse)
def get_watchlist(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    return _response(watchlist_service.load(db, user.id))


@router.put("", response_model=WatchlistResponse)
def replace_watchlist(
    payload: WatchlistUpdateRequest,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    try:
        state = watchlist_service.replace(
            db, user.id, payload.sids, payload.group_by_sid
        )
    except watchlist_service.WatchlistError as exc:
        raise _http(exc) from exc
    # Returns the resulting list so the client can setQueryData without a
    # follow-up GET.
    return _response(state)


@router.post("/groups", response_model=WatchlistResponse)
def create_group(
    payload: WatchlistGroupCreateRequest,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    try:
        state = watchlist_service.create_group(db, user.id, payload.name)
    except watchlist_service.WatchlistError as exc:
        raise _http(exc) from exc
    return _response(state)


@router.patch("/groups/{group_id}", response_model=WatchlistResponse)
def rename_group(
    group_id: int,
    payload: WatchlistGroupUpdateRequest,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    try:
        state = watchlist_service.rename_group(db, user.id, group_id, payload.name)
    except watchlist_service.WatchlistError as exc:
        raise _http(exc) from exc
    return _response(state)


@router.delete("/groups/{group_id}", response_model=WatchlistResponse)
def delete_group(
    group_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    try:
        state = watchlist_service.delete_group(db, user.id, group_id)
    except watchlist_service.WatchlistError as exc:
        raise _http(exc) from exc
    return _response(state)
