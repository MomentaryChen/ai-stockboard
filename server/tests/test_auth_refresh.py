"""Refresh-token rotation and replay detection.

A rotated token that is presented again is treated as stolen: every session
the user has is revoked, not just the one that was replayed. That is the
behaviour `frontend/src/api/client.ts` single-flights to avoid triggering
from the browser by accident.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RefreshToken
from app.services.auth import (
    InvalidTokenError,
    issue_tokens,
    rotate_refresh_token,
)

from tests.helpers import add_user


def _live_tokens(db: Session, user_id: int) -> list[RefreshToken]:
    return list(
        db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


def test_rotate_issues_a_new_pair_and_revokes_the_old_one(db: Session):
    user = add_user(db)
    _access, refresh, _expires = issue_tokens(db, user)

    _user, _new_access, new_refresh, _new_expires = rotate_refresh_token(db, refresh)

    assert new_refresh != refresh
    live = _live_tokens(db, user.id)
    assert len(live) == 1
    assert live[0].revoked_at is None


def test_replaying_a_rotated_token_revokes_every_session(db: Session):
    user = add_user(db)
    _a1, refresh_phone, _ = issue_tokens(db, user)
    _a2, refresh_laptop, _ = issue_tokens(db, user)
    assert len(_live_tokens(db, user.id)) == 2

    _user, _a3, refresh_phone_next, _ = rotate_refresh_token(db, refresh_phone)
    assert len(_live_tokens(db, user.id)) == 2  # laptop + new phone

    with pytest.raises(InvalidTokenError, match="Invalid refresh token"):
        rotate_refresh_token(db, refresh_phone)

    # Family wipe: the laptop session and the already-rotated phone session
    # are both gone. A stolen copy of the old phone token cannot be used to
    # keep *any* of this user's sessions alive.
    assert _live_tokens(db, user.id) == []
    with pytest.raises(InvalidTokenError):
        rotate_refresh_token(db, refresh_laptop)
    with pytest.raises(InvalidTokenError):
        rotate_refresh_token(db, refresh_phone_next)


def test_unknown_token_does_not_wipe_other_sessions(db: Session):
    user = add_user(db)
    _access, refresh, _ = issue_tokens(db, user)

    with pytest.raises(InvalidTokenError, match="Invalid refresh token"):
        rotate_refresh_token(db, "this-token-was-never-issued")

    assert len(_live_tokens(db, user.id)) == 1
    # The live one still rotates, so a typo on one device is not a lockout.
    rotate_refresh_token(db, refresh)
    assert len(_live_tokens(db, user.id)) == 1
