"""FastAPI dependencies for authentication and role checks.

Kept out of `app/services/` so the service layer stays framework-free: services
raise their own errors (`InvalidTokenError`, `UnknownStockError`), this module
is where they become HTTP responses.

The 401/403 split is load-bearing, because the frontend interceptor refreshes on
401 and gives up on 403:

  * missing, malformed or expired token  -> 401, and the client refreshes
  * valid token, disabled or wrong role  -> 403, and the client stops

Never answer an expired token with 403, or the browser signs the user out
instead of quietly renewing their session.

There are two authenticated dependencies, and the difference is load-bearing:

  * `get_authenticated_user` establishes *who* is calling and nothing more.
  * `get_current_user` adds the check that the account is in a usable state --
    today that means it is not sitting on an ADMIN-issued temporary password.

Everything takes `get_current_user`. Exactly two routes take the bare one, and
they are the two that let a user out of that state: `GET /api/auth/me`, so the
client can discover *why* it is blocked, and `POST /api/auth/me/password`, so it
can stop being blocked. Adding a third is almost certainly a mistake.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import security
from app.db import get_db
from app.models import ROLE_ADMIN, AppUser
from app.services import auth as auth_service

# auto_error=False so the 401 body matches the rest of the API. Declaring the
# scheme is also what gives /docs its Authorize button -- and unlike
# OAuth2PasswordRequestForm it needs no python-multipart dependency.
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}

# The frontend narrows on these exact strings rather than showing them, so they
# are part of the contract -- do not reword one without updating
# frontend/src/api/client.ts, which keeps a copy of each.
#
#   * PASSWORD_RESET_REQUIRED -> redirect to the change-password page
#   * ACCOUNT_PENDING_APPROVAL -> "waiting for an administrator", not "wrong
#     password" and not "you were banned"
#   * ACCOUNT_LOCKED / TOO_MANY_ATTEMPTS -> a countdown, from the Retry-After
#     header that accompanies both
PASSWORD_RESET_REQUIRED = "Password reset required"
ACCOUNT_PENDING_APPROVAL = "Account is awaiting approval"
ACCOUNT_LOCKED = "Account temporarily locked"
TOO_MANY_ATTEMPTS = "Too many sign-in attempts"


def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AppUser:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Not authenticated", headers=_UNAUTHENTICATED
        )

    user_id = security.read_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers=_UNAUTHENTICATED,
        )

    user = auth_service.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Account no longer exists",
            headers=_UNAUTHENTICATED,
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    return user


def get_current_user(user: AppUser = Depends(get_authenticated_user)) -> AppUser:
    """An authenticated user who is also allowed to use the rest of the API.

    403 rather than 401: the token is perfectly valid, so a refresh would not
    help and the client must not treat this as an expired session. It is the
    same status the disabled-account case returns, and the frontend already
    stops instead of retrying on 403.
    """
    if user.must_change_password:
        raise HTTPException(status_code=403, detail=PASSWORD_RESET_REQUIRED)
    return user


def require_role(*roles: str):
    """Dependency factory. Bind the result once at module level, not inline.

    Each call returns a fresh closure, and FastAPI's per-request dependency
    cache keys on callable identity -- writing `Depends(require_role("ADMIN"))`
    on ten routes would create ten distinct callables.
    """

    def dependency(user: AppUser = Depends(get_current_user)) -> AppUser:
        # Authorised from the database row, never from a claim in the token, so
        # a demotion takes effect on the next request instead of at token expiry.
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


require_admin = require_role(ROLE_ADMIN)
