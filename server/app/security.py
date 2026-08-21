"""Password hashing and JWT signing.

Deliberately free of database concerns: `services/auth.py` owns the rows, this
module owns the primitives.
"""

import datetime
import hashlib
import logging
import secrets

import bcrypt
import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MIN_PASSWORD_LENGTH = 8
# bcrypt only ever hashes the first 72 bytes of a password, and bcrypt 5 raises
# instead of truncating silently. Callers must reject longer input up front --
# a Chinese character is 3 bytes, so 24 of them already reach the cap.
MAX_PASSWORD_BYTES = 72

ACCESS_TOKEN_TYPE = "access"


def _resolve_secret() -> str:
    if settings.jwt_secret:
        return settings.jwt_secret
    logger.warning(
        "JWT_SECRET is not set -- generated an ephemeral one for this process. "
        "Every restart signs all users out, and running more than one uvicorn "
        "worker would issue tokens the other workers reject. "
        "Set JWT_SECRET in deployment/.env."
    )
    return secrets.token_urlsafe(48)


# Resolved once at import: the warning above should fire on boot, not per request.
_SECRET = _resolve_secret()


def password_problem(raw: str) -> str | None:
    """None when the password is usable, otherwise the reason it is not."""
    if len(raw) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(raw.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes once UTF-8 encoded"
    return None


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# Deliberately not the full alphabet: this password is read off a screen and
# typed by hand, so the pairs that look alike in most fonts (0/O, 1/l/I) are
# gone, and so are the symbols that move around on non-US keyboard layouts.
# 51 usable characters over 14 positions is ~79 bits, far past what the
# short-lived credential this produces needs.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
TEMP_PASSWORD_LENGTH = 14


def generate_temp_password() -> str:
    """A one-off password for an ADMIN-initiated reset.

    `secrets.choice`, not `random`: this is a credential. The result always
    satisfies `password_problem`, so a reset can never be rejected by the same
    validation that guards a user-chosen password.
    """
    return "".join(
        secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH)
    )


_dummy_hash: str | None = None


def _get_dummy_hash() -> str:
    """A real hash of a throwaway value, compared against when no account matched.

    Without it, "no such user" returns far faster than "wrong password" and the
    login endpoint becomes an oracle for which accounts exist. Built lazily so
    the ~250ms bcrypt cost is not paid on every import.

    Worth what it costs only because the number of guesses is bounded
    elsewhere: `services/auth.attempt_login` locks an account after enough
    consecutive failures, and `services/login_guard` limits attempts per source
    address. Closing a timing side channel while allowing unlimited guessing
    would be locking a window next to an open door.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


def verify_password(raw: str, hashed: str | None) -> bool:
    target = hashed or _get_dummy_hash()
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), target.encode("utf-8"))
    except ValueError:
        # Malformed hash in the row, or a password past bcrypt's 72-byte cap.
        return False


def create_access_token(user_id: int) -> tuple[str, int]:
    """Returns (token, seconds until it expires)."""
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        # RFC 7519 wants `sub` to be a string, and PyJWT 2.10+ enforces it.
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, _SECRET, algorithm=settings.jwt_algorithm)
    return token, expires_in


def read_access_token(token: str) -> int | None:
    """The user id carried by a valid access token, or None if it is not one.

    Note the token carries no role: `deps.get_current_user` reads the user row
    instead, so a demotion or a deactivation takes effect immediately rather
    than whenever the access token happens to expire.
    """
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError:
        return None

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def new_refresh_token() -> str:
    """The opaque value handed to the client. Only its digest is persisted."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # Plain SHA-256 is the right tool: the token is 48 random bytes, so there is
    # no low-entropy secret to brute force and bcrypt would only add latency.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
