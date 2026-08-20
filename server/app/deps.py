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


def get_current_user(
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
