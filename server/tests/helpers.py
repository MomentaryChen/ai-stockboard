"""Insert helpers for the SQLite account-table fixture."""

import datetime

from sqlalchemy.orm import Session

from app import security
from app.models import AppUser


def add_user(
    db: Session,
    *,
    user_id: int = 1,
    username: str = "alice",
    email: str = "alice@example.com",
    password: str = "password12",
    must_change_password: bool = False,
) -> AppUser:
    """Insert a user with an explicit id -- SQLite will not autoincrement BIGINT."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user = AppUser(
        id=user_id,
        username=username,
        email=email,
        password_hash=security.hash_password(password),
        role="USER",
        is_active=True,
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    return user
