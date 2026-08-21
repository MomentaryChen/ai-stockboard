"""Status vocabulary of the background-job handlers.

Only the credential cleanup is covered here, and only for the status it
reports: `last_success_at` is computed from `success` rows alone, so a handler
that returns `skipped` after a completed run silently ages itself out in the
admin console. That is a wiring mistake no assertion about deleted counts would
have caught.
"""

import datetime

from sqlalchemy.orm import Session

from app.models import RefreshToken
from app.services.jobs import handlers
from app.services.jobs.registry import JobContext

from tests.helpers import add_user


def _context(db: Session) -> JobContext:
    return JobContext(db=db, trigger="schedule", force=False, freshness_seconds=86_400)


def _add_expired_token(db: Session, user_id: int) -> None:
    db.add(
        RefreshToken(
            token_hash="a" * 64,
            user_id=user_id,
            expires_at=datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1),
            revoked_at=None,
        )
    )
    db.commit()


def test_cleanup_reports_success_when_there_was_nothing_to_delete(db: Session):
    add_user(db)

    result = handlers.refresh_token_cleanup(_context(db))

    # The sweep ran and the table was clean -- a finished run, not a skipped one.
    assert result.status == "success"
    assert result.stats == {"deleted": 0}
    assert result.message  # says why the count is zero


def test_cleanup_reports_success_when_it_deleted_something(db: Session):
    user = add_user(db)
    _add_expired_token(db, user.id)

    result = handlers.refresh_token_cleanup(_context(db))

    assert result.status == "success"
    assert result.stats == {"deleted": 1}
    assert result.message is None
