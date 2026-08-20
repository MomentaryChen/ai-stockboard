"""SQLAlchemy engine / session wiring.

Deliberately synchronous: twstock does blocking `requests` calls anyway, and
FastAPI runs plain `def` route handlers in a threadpool, so nothing blocks the
event loop. This keeps us clear of async driver complexity.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    future=True,
    # Fail fast instead of hanging the whole app when PostgreSQL is not up yet.
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
