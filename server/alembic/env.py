"""Alembic environment. Two callers share it.

  * The CLI (`uv run alembic ...`), which opens its own short-lived Engine.
  * `app.migrations`, which runs `upgrade head` during startup and hands its
    Engine over in `config.attributes["engine"]` rather than building a second
    connection pool inside a process that already has one.

The URL comes from `app.config` in both cases -- see the note in alembic.ini
for why it is not spelled out there.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  -- registers every table on Base.metadata
from app.config import get_settings
from app.db import Base

config = context.config

# The application already configured logging by the time it calls us; only the
# CLI wants alembic.ini's handlers.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it -- `alembic upgrade head --sql`.

    This is the reviewable artefact for a database nobody wants an application
    to hold DDL rights on.
    """
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Autogenerate is only as useful as what it notices. Types and server
        # defaults are exactly the drift `create_all` used to hide.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = config.attributes.get("engine")
    disposable = engine is None
    if disposable:
        engine = create_engine(_url(), poolclass=pool.NullPool, future=True)

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if disposable:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
