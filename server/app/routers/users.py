"""User administration. Every route here is ADMIN only.

Two lockout guards are enforced, because losing the last administrator means
losing the ability to fix it from the UI at all:

  * an admin cannot change their own role or active flag
  * the last active ADMIN cannot be demoted, suspended or deleted

The env-seeded account in app/services/auth.py is the remaining escape hatch.

`POST /{user_id}/password-reset` is the one route here that returns a secret.
It exists because the service has no mail delivery: the generated password
comes back in the response body for the admin to relay out of band, which is
why it is ADMIN-only and why the account it lands on is restricted until the
user replaces it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import AppUser
from app.schemas import (
    PasswordResetResponse,
    UserListResponse,
    UserOut,
    UserUpdateRequest,
)
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


@router.post("/{user_id}/password-reset", response_model=PasswordResetResponse)
def reset_user_password(
    user_id: int,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PasswordResetResponse:
    """Issue a generated password for a user who cannot sign in.

    The plaintext is in the response and nowhere else -- it is not stored, not
    logged, and cannot be fetched again. Losing it means running another reset.

    Resetting your own password is refused, matching the other self-service
    guards on this router. It is not a lockout risk, it is a wrong tool: an
    admin who wants a new password for themselves has POST /api/auth/me/password
    and does not need to be dropped into the restricted mode this sets.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="Use /api/auth/me/password to change your own password",
        )

    user = _load(db, user_id)
    temp_password = auth_service.admin_reset_password(db, user)
    return PasswordResetResponse(
        user=auth_service.to_user_out(user), temp_password=temp_password
    )


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
