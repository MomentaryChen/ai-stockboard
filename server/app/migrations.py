"""Run Alembic at startup, so a deployment upgrades itself.

The alternative -- expecting an operator to run `alembic upgrade head` by hand
before starting the new image -- does not survive `docker compose up -d --build`,
which is how this project is actually deployed. Migrating in the lifespan hook
keeps that one command sufficient.

Two things make that safe enough to do unattended:

  * a PostgreSQL advisory lock, so several replicas booting at once do not run
    the same DDL concurrently. The losers block until the winner finishes and
    then find nothing left to do.
  * `upgrade head` is a no-op when the database is already current, which is
    every boot after the first.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

# server/app/migrations.py -> server/
_SERVER_ROOT = Path(__file__).resolve().parents[1]

# Arbitrary but fixed: every replica has to name the same number, and it wants
# to be distinctive enough not to collide with a lock some other application
# takes on a shared database.
#   python -c "import zlib; print(zlib.crc32(b'ai-stockboard.migrations'))"
_MIGRATION_LOCK_KEY = 1009591546

# How long a replica waits for whichever one is migrating. Long enough for a
# real migration on a real table, short enough that a stuck lock shows up as a
# failed boot rather than a container that hangs forever.
_LOCK_TIMEOUT_MS = 5 * 60 * 1000


def _config(engine: Engine) -> Config:
    config = Config(str(_SERVER_ROOT / "alembic.ini"))
    # alembic.ini's script_location is relative to the working directory, which
    # for the CLI is server/ but for the application is wherever it was
    # launched from.
    config.set_main_option("script_location", str(_SERVER_ROOT / "alembic"))
    # env.py opens its own Engine unless handed one, and the application
    # already has a pool; and its logging is configured, so alembic.ini's
    # handlers must not replace it.
    config.attributes["engine"] = engine
    config.attributes["configure_logging"] = False
    return config


def upgrade_to_head(engine: Engine) -> None:
    """Apply every revision the database is behind. Idempotent."""
    # AUTOCOMMIT because this connection exists only to hold the advisory lock:
    # an open transaction alongside the migration's own would be idle for the
    # whole upgrade for no reason.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        lock.execute(text(f"set lock_timeout = {_LOCK_TIMEOUT_MS}"))
        lock.execute(text("select pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        try:
            command.upgrade(_config(engine), "head")
        finally:
            lock.execute(
                text("select pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY}
            )

    logger.info("Database schema is at head")
