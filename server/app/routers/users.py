"""User administration. Every route here is ADMIN only.

Two lockout guards are enforced, because losing the last administrator means
losing the ability to fix it from the UI at all:

  * an admin cannot change their own role or active flag
  * the last active ADMIN cannot be demoted, suspended or deleted

The env-seeded account in app/services/auth.py is the remaining escape hatch.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import AppUser
from app.schemas import UserListResponse, UserOut, UserUpdateRequest
from app.services import auth as auth_service

router = APIRouter(prefix="/api/users", tags=["users"])


def _load(db: Session, user_id: int) -> AppUser:
    user = auth_service.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    return user


@router.get("", response_model=UserListResponse)
def list_users(
    q: str | None = Query(None, description="以帳號或 Email 片段過濾"),
    limit: int = Query(50, ge=1, le=200, description="每頁筆數"),
    offset: int = Query(0, ge=0, description="略過前幾筆"),
    _admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserListResponse:
    total, rows = auth_service.list_users(db, q=q, limit=limit, offset=offset)
    return UserListResponse(
        total=total, users=[auth_service.to_user_out(u) for u in rows]
    )


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    _admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    return auth_service.to_user_out(_load(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    if user_id == admin.id:
        raise HTTPException(
            status_code=400, detail="Cannot change your own role or status"
        )

    user = _load(db, user_id)
    try:
        updated = auth_service.update_user(
            db, user, role=payload.role, is_active=payload.is_active
        )
    except auth_service.LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth_service.InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return auth_service.to_user_out(updated)


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    user = _load(db, user_id)
    try:
        auth_service.delete_user(db, user)
    except auth_service.LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
