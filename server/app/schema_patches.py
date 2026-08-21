"""Additive DDL that `create_all` cannot perform on its own.

`Base.metadata.create_all` only ever creates *missing tables*. A column added
to an existing model is therefore invisible to a database that already holds
data, and the first query naming it fails with UndefinedColumn.

This project has no migration tool (see the note in app/models.py), so each
column added after a table shipped gets one idempotent `ADD COLUMN IF NOT
EXISTS` here. They run on every boot, are cheap when the column already exists,
and keep a running deployment upgradable without hand-written SQL.

Rules for anything added to `_PATCHES`:

  * additive only -- never DROP, never a type change, never a rename. Those are
    not safe to run unattended against a live table.
  * a new NOT NULL column needs a DEFAULT, or the statement fails on any table
    that already has rows.
  * the statement must match what `models.py` declares, because a fresh
    database gets its column from `create_all` and never sees this file.
"""

import logging

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

# (label for the log, statement). PostgreSQL 9.6+ for ADD COLUMN IF NOT EXISTS.
_PATCHES: tuple[tuple[str, str], ...] = (
    (
        "app_user.must_change_password",
        "alter table app_user "
        "add column if not exists must_change_password boolean not null default false",
    ),
    # Defaults false, so every account that already exists reads as reviewed
    # rather than appearing in the admin's approval queue on the next boot.
    (
        "app_user.pending_approval",
        "alter table app_user "
        "add column if not exists pending_approval boolean not null default false",
    ),
    (
        "app_user.failed_login_count",
        "alter table app_user "
        "add column if not exists failed_login_count integer not null default 0",
    ),
    (
        "app_user.locked_until",
        "alter table app_user "
        "add column if not exists locked_until timestamptz",
    ),
)


def apply(engine: Engine) -> None:
    """Bring an existing database up to what the models declare. Idempotent."""
    for label, statement in _PATCHES:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception:
            # One failed patch must not stop the others, nor the boot: the app
            # still needs to come up far enough for /api/health to say why.
            logger.exception("Schema patch failed: %s", label)
        else:
            logger.debug("Schema patch ok: %s", label)
