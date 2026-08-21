"""Shared fixtures. Env vars are set before any `app` import so Settings and
the JWT secret resolve to test values rather than a developer's deployment/.env
or the ephemeral-secret warning.
"""

import os

os.environ.setdefault("JWT_SECRET", "ci-test-secret-must-be-32-bytes-ok")
os.environ.setdefault("JOBS_SCHEDULER_ENABLED", "false")
os.environ.setdefault("ADMIN_EMAIL", "")
os.environ.setdefault("ADMIN_PASSWORD", "")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import AppUser, RefreshToken


@pytest.fixture
def db() -> Session:
    """SQLite stand-in with just the account tables.

    The rest of the schema uses PostgreSQL JSONB, so create_all is not an
    option. Replay detection and password-reset only touch these two.
    """
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    AppUser.__table__.create(engine)
    RefreshToken.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
