"""Registration, sign-in and the signed-in user's own profile.

Bodies are JSON throughout rather than OAuth2PasswordRequestForm: that would
pull in python-multipart and force a username/password form encoding, which
fits neither the "帳號或 Email" field nor the JSON contract every other endpoint
in this service uses. /docs still gets an Authorize button, from the HTTPBearer
scheme declared in app/deps.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_authenticated_user, get_current_user
from app.models import AppUser
from app.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth as auth_service
from app.services import watchlist as watchlist_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_response(
    user: AppUser, access_token: str, refresh_token: str, expires_in: int
) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=auth_service.to_user_out(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = auth_service.create_user(
            db,
            username=payload.username,
            email=str(payload.email),
            password=payload.password,
            phone=payload.phone,
        )
    except auth_service.DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth_service.InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Same starting board an anonymous visitor gets, so signing up never lands
    # on an empty watchlist.
    watchlist_service.seed_default(db, user.id)

    # Registering signs you in, so the client can merge a locally stored
    # watchlist straight away instead of making a second round trip.
    access_token, refresh_token, expires_in = auth_service.login(db, user)
    return _token_response(user, access_token, refresh_token, expires_in)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = auth_service.authenticate(db, payload.identifier, payload.password)
    if user is None:
        # Deliberately identical for "no such account" and "wrong password".
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    access_token, refresh_token, expires_in = auth_service.login(db, user)
    return _token_response(user, access_token, refresh_token, expires_in)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user, access_token, refresh_token, expires_in = (
            auth_service.rotate_refresh_token(db, payload.refresh_token)
        )
    except auth_service.InvalidTokenError as exc:
        raise HTTPException(
            status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    return _token_response(user, access_token, refresh_token, expires_in)


@router.post("/logout", status_code=204)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:
    # No access token required: logging out has to work after the access token
    # has already expired. Revoking an unknown token is a no-op, not an error.
    auth_service.revoke_refresh_token(db, payload.refresh_token)


@router.get("/me", response_model=UserOut)
def read_me(user: AppUser = Depends(get_authenticated_user)) -> UserOut:
    # get_authenticated_user, not get_current_user: an account sitting on an
    # ADMIN-issued temporary password has to be able to read itself, or the
    # client cannot discover that `must_change_password` is what is blocking it.
    return auth_service.to_user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: ProfileUpdateRequest,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    try:
        updated = auth_service.update_profile(
            db,
            user,
            email=str(payload.email) if payload.email is not None else None,
            phone=payload.phone,
        )
    except auth_service.DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return auth_service.to_user_out(updated)


@router.post("/me/password", response_model=TokenResponse)
def change_password(
    payload: PasswordChangeRequest,
    user: AppUser = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Set a new password. The only way out of a forced reset.

    Takes `get_authenticated_user` so it stays reachable while
    `must_change_password` is set -- gating it behind `get_current_user` would
    lock the account out of the one action that unlocks it.

    The current password is still required, which an ADMIN-issued temporary one
    satisfies: the user was told what it is. That keeps a single code path for
    both the forced and the voluntary case, and means a temporary password left
    open on a colleague's screen cannot be swapped for a permanent one by
    somebody who never knew it.
    """
    if not auth_service.authenticate(db, user.username, payload.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    try:
        auth_service.set_password(db, user, payload.new_password)
    except auth_service.InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Changing a password ends every session the account has, including this
    # one -- there is no way to tell "the caller's refresh token" apart from a
    # stolen copy of it. The caller is then handed a fresh pair, so the device
    # that just proved it knows the new password stays signed in while all the
    # others are dropped. Returning tokens is what makes the forced-reset flow
    # survivable: without it the user would be signed out minutes after
    # choosing their password, when the access token expired with no live
    # refresh token behind it.
    auth_service.revoke_all_for_user(db, user.id)
    access_token, refresh_token, expires_in = auth_service.issue_tokens(db, user)
    return _token_response(user, access_token, refresh_token, expires_in)
