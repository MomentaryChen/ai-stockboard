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

`get_optional_user` is the odd one out: it answers `None` instead of 401ing, and
exists for the public market-data routes, which serve anonymous callers but hand
a signed-in one a wider upstream budget. See the fetch-budget section below.
"""

from fastapi import Depends, HTTPException, Query
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


# --- upstream fetch budget -------------------------------------------------
#
# TWSE allows this service 3 requests per 5 seconds, from one source IP, shared
# by every caller. The routes below are public because they answer out of the
# PostgreSQL cache -- but a cache miss still reaches the exchange, so a public
# route can spend a budget the whole board depends on. `/api/realtime` is behind
# a sign-in for exactly this reason; what follows stops the cached routes from
# being a way around that.

#: Months of daily bars an anonymous caller may ask us to backfill at once, and
#: the widest range the public chart offers (frontend/src/pages/StockDetail.tsx).
#: A signed-in caller keeps the full 24, because the spend is attributable.
ANONYMOUS_MAX_MONTHS = 12

#: Same idea for ex-dividend years. Each year is its own upstream report.
ANONYMOUS_MAX_YEARS = 5


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AppUser | None:
    """The caller, when there is one. Does not 401 on a *missing* token.

    A token that is present but bad still 401s, so a session expiring mid-poll
    reaches the frontend's refresh interceptor instead of silently demoting the
    user to the anonymous tier and halving their chart range.
    """
    if credentials is None:
        return None
    return get_current_user(get_authenticated_user(credentials, db))


def force_refresh(
    force: bool = Query(False, description="忽略快取，強制向 TWSE/TPEX 重抓（需 ADMIN）"),
    user: AppUser | None = Depends(get_optional_user),
) -> bool:
    """Owns the `force` query parameter, and refuses it to non-ADMINs.

    `force` skips every cache check, so one request re-fetches every bucket in
    its range and the next identical request does it again -- the one knob on a
    public route that an attacker can pull without limit. Twenty-four months of
    it is ~44 seconds of the service's entire TWSE allowance, spent by someone
    who never signed in, while `/api/realtime` politely queues behind it.

    Declared as a dependency rather than repeated on each route so the rule
    cannot drift between `/history`, `/dividends` and `/chips`, and so a new
    route that wants a force switch gets the check by taking this instead of
    a bare bool.
    """
    if not force:
        return False
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in as ADMIN to force a refresh",
            headers=_UNAUTHENTICATED,
        )
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only an ADMIN may force a refresh"
        )
    return True


def regenerate_ai(
    regenerate: bool = Query(
        False, description="忽略已存的判讀，重新請 AI 生成一次（需 ADMIN）"
    ),
    user: AppUser | None = Depends(get_optional_user),
) -> bool:
    """The AI equivalent of `force_refresh`, and ADMIN-only for the same reason.

    An AI verdict is cached per (stock, trading day, model, prompt), which is
    what stops a watchlist board from billing once per card per visitor.
    `regenerate` is the switch that turns that cached route back into a metered
    one -- except the meter here is a bill rather than a rate limit, so it is
    kept away from the ordinary daily quota entirely.

    Its legitimate use is checking a prompt change against a stock whose verdict
    is already stored, which is an operator's job, not a reader's.
    """
    if not regenerate:
        return False
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in as ADMIN to regenerate an AI verdict",
            headers=_UNAUTHENTICATED,
        )
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only an ADMIN may regenerate an AI verdict"
        )
    return True


def limit_anonymous_window(
    user: AppUser | None, requested: int, anonymous_max: int, unit: str
) -> None:
    """Reject a range wider than an anonymous caller is allowed to spend.

    Refused rather than silently clamped: the response reports the range it
    answered for, and quietly returning half of it would make the chart look
    like the exchange has no older data.
    """
    if user is None and requested > anonymous_max:
        raise HTTPException(
            status_code=401,
            detail=f"Sign in to request more than {anonymous_max} {unit}",
            headers=_UNAUTHENTICATED,
        )
