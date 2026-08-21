"""Registration, sign-in and the signed-in user's own profile.

Bodies are JSON throughout rather than OAuth2PasswordRequestForm: that would
pull in python-multipart and force a username/password form encoding, which
fits neither the "帳號或 Email" field nor the JSON contract every other endpoint
in this service uses. /docs still gets an Authorize button, from the HTTPBearer
scheme declared in app/deps.py.

Two gates sit on the unauthenticated routes here, and both exist because
/api/realtime is signed-in only so that the upstream quota it spends has a name
against it:

  * registration is reviewed. A new account lands dormant and an ADMIN
    activates it from /admin/users. Without that, "you must sign in" costs an
    attacker ten seconds and the attribution is worth nothing.
  * sign-in is throttled on two dimensions -- per account in the database, per
    source address in memory. See services/login_guard.py for why the two
    halves are stored differently and what each one does not cover.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import (
    ACCOUNT_LOCKED,
    ACCOUNT_PENDING_APPROVAL,
    TOO_MANY_ATTEMPTS,
    get_authenticated_user,
    get_current_user,
)
from app.models import AppUser
from app.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    RegistrationPolicy,
    TokenResponse,
    UserOut,
)
from app.services import auth as auth_service
from app.services import login_guard
from app.services import watchlist as watchlist_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _too_many(detail: str, retry_after: int) -> HTTPException:
    """429 with the wait attached.

    Retry-After is the part the UI actually renders -- it is the difference
    between "try again later" and "try again in 12 minutes", and the latter is
    what stops a locked-out user from hammering the endpoint for the whole
    window.
    """
    return HTTPException(
        status_code=429, detail=detail, headers={"Retry-After": str(retry_after)}
    )


def _token_response(
    user: AppUser, access_token: str, refresh_token: str, expires_in: int
) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=auth_service.to_user_out(user),
    )


@router.get("/registration-policy", response_model=RegistrationPolicy)
def registration_policy() -> RegistrationPolicy:
    """What signing up will do. Unauthenticated by necessity -- the people who
    need the answer are exactly the ones without an account."""
    return RegistrationPolicy(
        open=True, requires_approval=settings.registration_requires_approval
    )


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(
    payload: RegisterRequest, request: Request, db: Session = Depends(get_db)
) -> RegisterResponse:
    """Create an account, and sign in with it unless it needs reviewing.

    201 either way: the row is created in both cases, and `pending` in the body
    is what says whether it may be used yet. A 202 for the review path would
    read better in isolation but would make the client branch on the status
    code *and* the body, for one bit of information.
    """
    source = login_guard.client_ip(request)
    retry_after = login_guard.registrations.retry_after(source)
    if retry_after:
        # Rate limited even though nothing has gone wrong yet: a script that
        # can open accounts faster than an admin can review them turns the
        # review queue itself into the denial of service.
        raise _too_many("Too many accounts created from this address", retry_after)

    try:
        user = auth_service.create_user(
            db,
            username=payload.username,
            email=str(payload.email),
            password=payload.password,
            phone=payload.phone,
            pending_approval=settings.registration_requires_approval,
        )
    except auth_service.DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except auth_service.InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Counted after the insert, so a rejected duplicate or a too-short password
    # does not spend somebody's budget on an account that was never created.
    login_guard.registrations.record(source)

    # Same starting board an anonymous visitor gets, so signing up never lands
    # on an empty watchlist. Seeded even for an account awaiting review: it
    # costs one insert and it is what the account will want on the day it is
    # let in, whereas deferring it means finding somewhere to run it later.
    watchlist_service.seed_default(db, user.id)

    if user.pending_approval:
        # No tokens on purpose. The account exists and cannot be used, and a
        # token whose every request answers 403 would leave the client looking
        # signed in while nothing on the page worked.
        return RegisterResponse(pending=True, user=auth_service.to_user_out(user))

    # Registering signs you in, so the client can merge a locally stored
    # watchlist straight away instead of making a second round trip.
    access_token, refresh_token, expires_in = auth_service.login(db, user)
    return RegisterResponse(
        pending=False,
        user=auth_service.to_user_out(user),
        tokens=_token_response(user, access_token, refresh_token, expires_in),
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, db: Session = Depends(get_db)
) -> TokenResponse:
    source = login_guard.client_ip(request)
    retry_after = login_guard.login_failures.retry_after(source)
    if retry_after:
        raise _too_many(TOO_MANY_ATTEMPTS, retry_after)

    attempt = auth_service.attempt_login(db, payload.identifier, payload.password)

    if not attempt.ok:
        if attempt.locked_for:
            # Not counted against the address budget: the account is already
            # refusing to answer, so there is nothing left to protect here, and
            # counting it would punish the account's real owner for retrying.
            raise _too_many(ACCOUNT_LOCKED, attempt.locked_for)
        login_guard.login_failures.record(source)
        # Deliberately identical for "no such account" and "wrong password".
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_guard.login_failures.clear(source)

    user = attempt.user
    assert user is not None  # ok is only true with a user behind it

    # Checked after the password, not before: answering "that account is
    # awaiting approval" to whoever asks would turn the endpoint into a list of
    # who has signed up. You have to prove the account is yours first.
    if user.pending_approval:
        raise HTTPException(status_code=403, detail=ACCOUNT_PENDING_APPROVAL)
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
