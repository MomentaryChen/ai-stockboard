"""Accounts, credentials and refresh-token lifecycle.

Access tokens are stateless and carry only the user id; everything that decides
*what that user may do* is read from `app_user` on each request, so a demotion
or a deactivation takes effect at once rather than whenever the token expires.

Refresh tokens are the opposite: opaque random strings whose SHA-256 digest is
the only thing stored, rotated on every use, and revocable. Presenting a token
that has already been rotated away is treated as a leak and drops every session
the user has.

Two things here guard the front door rather than the session behind it:
`create_user` can land an account dormant for an ADMIN to approve, and
`attempt_login` counts consecutive failures and locks the account for a while
once there have been too many. The per-source-address half of that second
defence is not here -- see services/login_guard.py, which explains why the two
halves are stored differently.
"""

import dataclasses
import datetime
import logging

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import security
from app.config import get_settings
from app.models import ROLE_ADMIN, ROLE_USER, ROLES, AppUser, RefreshToken
from app.schemas import UserOut

logger = logging.getLogger(__name__)
settings = get_settings()

# Revoked rows are kept this long rather than deleted immediately, so replaying
# a rotated token still matches a row and trips reuse detection instead of
# looking like an unknown token.
REVOKED_RETENTION_DAYS = 7


class AuthError(Exception):
    """Base for the failures a router turns into an HTTP status."""


class DuplicateUserError(AuthError):
    def __init__(self, field: str):
        super().__init__(f"{field} is already registered")
        self.field = field


class InvalidInputError(AuthError):
    pass


class InvalidTokenError(AuthError):
    pass


class LastAdminError(AuthError):
    def __init__(self) -> None:
        super().__init__("At least one active administrator is required")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    """psycopg3 returns aware datetimes, but be defensive about naive ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


def to_user_out(user: AppUser) -> UserOut:
    """Shared: three routers return users and the schemas have no from_attributes."""
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        phone=user.phone,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        pending_approval=user.pending_approval,
        locked_until=user.locked_until,
        created_at=user.created_at,
    )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def get_by_id(db: Session, user_id: int) -> AppUser | None:
    return db.get(AppUser, user_id)


def find_by_identifier(db: Session, identifier: str) -> AppUser | None:
    """Look a user up by username *or* email -- login accepts either.

    Both sides are lowered so this uses the ux_app_user_*_lower functional
    indexes; the stored value keeps whatever casing the user typed.
    """
    value = identifier.strip().lower()
    if not value:
        return None
    stmt = select(AppUser).where(
        or_(func.lower(AppUser.username) == value, func.lower(AppUser.email) == value)
    )
    return db.execute(stmt).scalars().first()


def _taken(db: Session, column, value: str) -> bool:
    stmt = select(AppUser.id).where(func.lower(column) == value.strip().lower())
    return db.execute(stmt).first() is not None


def count_active_admins(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(AppUser)
        .where(AppUser.role == ROLE_ADMIN, AppUser.is_active.is_(True))
    )
    return db.execute(stmt).scalar_one()


def create_user(
    db: Session,
    username: str,
    email: str,
    password: str,
    phone: str | None = None,
    role: str = ROLE_USER,
    pending_approval: bool = False,
) -> AppUser:
    """Insert an account.

    `pending_approval` is what self-service registration passes; it lands the
    row dormant, to be activated by an ADMIN. Callers that already are an
    admin -- the env seed, and anything added later that creates accounts on
    an operator's behalf -- leave it false, because a review the operator would
    be performing on themselves is theatre.
    """
    username = username.strip()
    email = email.strip()

    if not 3 <= len(username) <= 32:
        raise InvalidInputError("Username must be between 3 and 32 characters")
    problem = security.password_problem(password)
    if problem:
        raise InvalidInputError(problem)
    if role not in ROLES:
        raise InvalidInputError("Role must be one of: " + ", ".join(ROLES))

    # Check first so the error names the offending field; the unique indexes
    # still catch the race between this check and the insert.
    if _taken(db, AppUser.username, username):
        raise DuplicateUserError("Username")
    if _taken(db, AppUser.email, email):
        raise DuplicateUserError("Email")

    user = AppUser(
        username=username,
        email=email,
        phone=(phone or "").strip() or None,
        password_hash=security.hash_password(password),
        role=role,
        # A pending account is inactive as well as flagged, so every existing
        # `is_active` check keeps it out without knowing this feature exists.
        # The flag says *why* it is inactive; `is_active` is what enforces it.
        is_active=not pending_approval,
        pending_approval=pending_approval,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateUserError("Username or email") from exc
    return user


def authenticate(db: Session, identifier: str, password: str) -> AppUser | None:
    """None for both "no such account" and "wrong password".

    Callers must not distinguish the two in their response, and
    security.verify_password burns the same bcrypt cost either way so the
    response timing does not distinguish them either.
    """
    user = find_by_identifier(db, identifier)
    stored = user.password_hash if user is not None else None
    if not security.verify_password(password, stored):
        return None
    return user


@dataclasses.dataclass(frozen=True)
class LoginAttempt:
    """What one sign-in attempt established. The router turns it into a status.

    `user` is filled in even when the password was wrong, so the caller can see
    which account was targeted -- but it must not leak that into the response:
    "no such account" and "wrong password" have to stay indistinguishable.
    """

    user: AppUser | None
    ok: bool
    # Seconds until a locked account will answer again; 0 when it is not locked.
    locked_for: int = 0


def _lock_seconds_remaining(user: AppUser) -> int:
    if user.locked_until is None:
        return 0
    remaining = (_as_utc(user.locked_until) - _now()).total_seconds()
    return max(0, int(remaining) + 1) if remaining > 0 else 0


def attempt_login(db: Session, identifier: str, password: str) -> LoginAttempt:
    """Verify credentials and keep the per-account failure count up to date.

    The lock is checked *before* the password, which does mean a locked account
    answers faster than an unknown one. That leaks nothing: reaching the lock
    takes `LOGIN_MAX_FAILURES` failed attempts against that exact account, so
    whoever sees the 429 already knew the account was there. Verifying first
    would be worse than useless -- it would let somebody who has since guessed
    the password in, which is the one thing the lock exists to prevent.

    A correct password clears the counter even when the account turns out to be
    dormant. The counter is about credentials being guessed; whether the
    account may then be used is a separate question the caller asks next.
    """
    user = find_by_identifier(db, identifier)

    if user is not None:
        locked_for = _lock_seconds_remaining(user)
        if locked_for > 0:
            return LoginAttempt(user=user, ok=False, locked_for=locked_for)

    stored = user.password_hash if user is not None else None
    if not security.verify_password(password, stored):
        if user is not None:
            _record_failed_login(db, user)
        return LoginAttempt(user=user, ok=False)

    assert user is not None  # a hash only verifies when a row supplied it
    _clear_failed_logins(db, user)
    return LoginAttempt(user=user, ok=True)


def _record_failed_login(db: Session, user: AppUser) -> None:
    """Count one miss, and lock the account once there have been enough.

    This is a denial-of-service surface by construction: anybody who knows a
    username can spend five wrong passwords to keep its owner out for the
    lockout window. That trade is made knowingly -- the window is minutes
    rather than permanent, an admin can lift it from /admin/users, and the
    alternative (no account limit at all) means an unlimited password guessing
    budget, which is the worse of the two.
    """
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= settings.login_max_failures:
        user.locked_until = _now() + datetime.timedelta(
            minutes=settings.login_lockout_minutes
        )
        # Counted from zero again, so the next lock needs another full run of
        # failures rather than tripping on the first one after the window.
        user.failed_login_count = 0
        logger.warning(
            "Locked user_id=%s for %s minutes after %s failed sign-ins",
            user.id,
            settings.login_lockout_minutes,
            settings.login_max_failures,
        )
    db.commit()


def _clear_failed_logins(db: Session, user: AppUser) -> None:
    if user.failed_login_count == 0 and user.locked_until is None:
        return  # the common case: no write on an ordinary sign-in
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()


def unlock(db: Session, user: AppUser) -> AppUser:
    """Lift a lockout by hand. Idempotent on an account that is not locked."""
    _clear_failed_logins(db, user)
    return user


def set_password(db: Session, user: AppUser, new_password: str) -> None:
    """Set a password the user chose themselves.

    Clearing `must_change_password` here rather than at the call site is what
    lifts the restricted mode an ADMIN reset puts the account into -- there is
    no other way out of it, so the two must not be able to drift apart.
    """
    problem = security.password_problem(new_password)
    if problem:
        raise InvalidInputError(problem)
    user.password_hash = security.hash_password(new_password)
    user.must_change_password = False
    db.commit()


def update_profile(
    db: Session, user: AppUser, email: str | None = None, phone: str | None = None
) -> AppUser:
    if email is not None:
        email = email.strip()
        if email.lower() != user.email.lower() and _taken(db, AppUser.email, email):
            raise DuplicateUserError("Email")
        user.email = email
    if phone is not None:
        user.phone = phone.strip() or None
    db.commit()
    return user


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


def list_users(
    db: Session,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    pending_only: bool = False,
) -> tuple[int, list[AppUser]]:
    stmt = select(AppUser)
    if pending_only:
        stmt = stmt.where(AppUser.pending_approval.is_(True))
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(AppUser.username).like(pattern),
                func.lower(AppUser.email).like(pattern),
            )
        )

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()
    rows = db.execute(
        # Accounts waiting for review come first, because they are the only
        # rows on this page that need somebody to do something. Everything
        # else keeps the stable id order it had.
        stmt.order_by(AppUser.pending_approval.desc(), AppUser.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return total, list(rows)


def count_pending_approvals(db: Session) -> int:
    """How many accounts are waiting for an ADMIN. Drives the dashboard tile."""
    stmt = (
        select(func.count())
        .select_from(AppUser)
        .where(AppUser.pending_approval.is_(True))
    )
    return db.execute(stmt).scalar_one()


def _would_strand_the_system(db: Session, user: AppUser) -> bool:
    """True when removing this user's admin access would leave nobody in charge."""
    if user.role != ROLE_ADMIN or not user.is_active:
        return False
    return count_active_admins(db) <= 1


def update_user(
    db: Session,
    user: AppUser,
    role: str | None = None,
    is_active: bool | None = None,
) -> AppUser:
    losing_admin = (role is not None and role != ROLE_ADMIN) or is_active is False
    if losing_admin and _would_strand_the_system(db, user):
        raise LastAdminError()

    if role is not None:
        if role not in ROLES:
            raise InvalidInputError("Role must be one of: " + ", ".join(ROLES))
        user.role = role
    if is_active is not None:
        user.is_active = is_active
        # Activating *is* the approval -- there is no separate button, so the
        # flag has to come down here or the account would stay in the review
        # queue forever while being perfectly able to sign in.
        if is_active:
            user.pending_approval = False
            # An account that has never signed in cannot have a lockout worth
            # keeping, and an admin reaching for the activate button after a
            # suspension means to hand the account back, not to hand it back
            # with a timer still running on it.
            user.failed_login_count = 0
            user.locked_until = None
    db.commit()

    # A demoted or suspended account must not keep renewing its session.
    if losing_admin or is_active is False:
        revoke_all_for_user(db, user.id)
    return user


def admin_reset_password(db: Session, user: AppUser) -> str:
    """Replace a user's password with a generated one and return it *once*.

    The plaintext is the return value and is never stored, logged or
    retrievable afterwards -- only its bcrypt hash goes to the database, same
    as any other password. If the admin loses it before handing it over, the
    only remedy is another reset.

    Two things happen alongside the new hash, and both matter:

      * every refresh token is revoked, so a session opened with the old
        password (or by whoever prompted the reset) dies immediately rather
        than surviving on rotation for another week.
      * `must_change_password` is raised, which puts the account in the
        restricted mode `deps.get_current_user` enforces: it can read its own
        profile and set a new password, nothing else. Without it a password
        that travelled through chat or email would be a working credential for
        as long as the user left it alone.

    There is no email delivery in this service, so handing the plaintext back
    to the caller is the whole transport. It is why the route is ADMIN-only and
    why the flag above is not optional.
    """
    temp_password = security.generate_temp_password()
    user.password_hash = security.hash_password(temp_password)
    user.must_change_password = True
    db.commit()

    revoke_all_for_user(db, user.id)
    logger.info("ADMIN reset the password for user_id=%s", user.id)
    return temp_password


def delete_user(db: Session, user: AppUser) -> None:
    if _would_strand_the_system(db, user):
        raise LastAdminError()
    # refresh_token and watchlist_item rows go with it, via ON DELETE CASCADE.
    db.delete(user)
    db.commit()


# --------------------------------------------------------------------------
# Refresh tokens
# --------------------------------------------------------------------------


def _purge_stale_tokens(db: Session, user_id: int) -> None:
    """Opportunistic per-user cleanup -- cheaper than owning a scheduled job."""
    cutoff = _now() - datetime.timedelta(days=REVOKED_RETENTION_DAYS)
    db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id,
            or_(RefreshToken.expires_at < _now(), RefreshToken.revoked_at < cutoff),
        )
    )


def purge_dead_refresh_tokens(db: Session) -> int:
    """Delete every refresh token nobody can do anything with, for every user.

    The per-user version above only runs when that user signs in, so the rows
    of an account that stopped logging in three months ago sit there forever.
    This is the same predicate applied across the table, and it is what the
    `refresh_token_cleanup` job calls.

    Revoked-but-recent rows are deliberately spared: reuse detection needs them
    to still be there, or a replayed token looks like one we never issued.
    """
    cutoff = _now() - datetime.timedelta(days=REVOKED_RETENTION_DAYS)
    result = db.execute(
        delete(RefreshToken).where(
            or_(RefreshToken.expires_at < _now(), RefreshToken.revoked_at < cutoff)
        )
    )
    db.commit()
    return result.rowcount or 0


def issue_tokens(db: Session, user: AppUser) -> tuple[str, str, int]:
    """A fresh (access, refresh, expires_in) triple for an authenticated user."""
    access_token, expires_in = security.create_access_token(user.id)

    raw_refresh = security.new_refresh_token()
    db.add(
        RefreshToken(
            token_hash=security.hash_refresh_token(raw_refresh),
            user_id=user.id,
            expires_at=_now()
            + datetime.timedelta(days=settings.refresh_token_expire_days),
        )
    )
    db.commit()
    return access_token, raw_refresh, expires_in


def login(db: Session, user: AppUser) -> tuple[str, str, int]:
    _purge_stale_tokens(db, user.id)
    return issue_tokens(db, user)


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    """Idempotent: logging out twice, or with a stale token, is not an error."""
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == security.hash_refresh_token(raw_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    db.commit()


def revoke_all_for_user(db: Session, user_id: int) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[AppUser, str, str, int]:
    """Trade a refresh token for a new pair, invalidating the one presented.

    Raises InvalidTokenError for anything that is not a live token. A token that
    was already rotated away means someone is replaying a copy, so every session
    that user has is dropped rather than just refusing this one request.
    """
    row = db.get(RefreshToken, security.hash_refresh_token(raw_token))
    if row is None:
        raise InvalidTokenError("Invalid refresh token")

    if row.revoked_at is not None:
        logger.warning(
            "Refresh token reuse detected for user_id=%s -- revoking all sessions",
            row.user_id,
        )
        revoke_all_for_user(db, row.user_id)
        raise InvalidTokenError("Invalid refresh token")

    if _as_utc(row.expires_at) < _now():
        raise InvalidTokenError("Refresh token has expired")

    user = get_by_id(db, row.user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("Account is unavailable")

    row.revoked_at = _now()
    db.flush()

    access_token, raw_refresh, expires_in = issue_tokens(db, user)
    return user, access_token, raw_refresh, expires_in


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


def seed_admin(db: Session) -> None:
    """Make sure the ADMIN configured in the environment exists. Idempotent.

    An existing account keeps its password -- overwriting it on every boot would
    turn .env into a permanent password-reset backdoor and would silently undo a
    password changed in the UI. Only the role is restored, which is deliberate:
    it is the way back in after an accidental self-demotion.
    """
    email = (settings.admin_email or "").strip()
    password = settings.admin_password or ""
    if not email or not password:
        logger.info("ADMIN_EMAIL / ADMIN_PASSWORD not set -- skipping admin seed")
        return

    existing = find_by_identifier(db, email)
    if existing is not None:
        if existing.role != ROLE_ADMIN or not existing.is_active:
            existing.role = ROLE_ADMIN
            existing.is_active = True
            db.commit()
            logger.warning("Restored ADMIN access for the seed account %s", email)
        return

    try:
        create_user(
            db,
            username=settings.admin_username,
            email=email,
            password=password,
            role=ROLE_ADMIN,
        )
    except AuthError:
        logger.exception("Could not seed the ADMIN account for %s", email)
        return
    logger.info("Seeded ADMIN account %s", email)
