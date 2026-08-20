"""Accounts, credentials and refresh-token lifecycle.

Access tokens are stateless and carry only the user id; everything that decides
*what that user may do* is read from `app_user` on each request, so a demotion
or a deactivation takes effect at once rather than whenever the token expires.

Refresh tokens are the opposite: opaque random strings whose SHA-256 digest is
the only thing stored, rotated on every use, and revocable. Presenting a token
that has already been rotated away is treated as a leak and drops every session
the user has.
"""

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
) -> AppUser:
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
        is_active=True,
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
    db: Session, q: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[int, list[AppUser]]:
    stmt = select(AppUser)
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
        stmt.order_by(AppUser.id).limit(limit).offset(offset)
    ).scalars()
    return total, list(rows)


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
