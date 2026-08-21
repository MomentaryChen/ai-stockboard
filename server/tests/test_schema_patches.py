"""`schema_patches.apply` is the migration tool this project has.

`create_all` never ALTERs an existing table. A column added after a table
shipped is therefore a boot-time `ADD COLUMN IF NOT EXISTS`, and that
statement has to be safe to run on every start -- including a database that
already has the column, and including a second process racing the first.

The SQL is PostgreSQL; SQLite rejects `IF NOT EXISTS` on ADD COLUMN in some
builds, so idempotency is pinned on the statement text plus `apply` actually
emitting it every boot rather than on a SQLite round-trip.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

from app import schema_patches


def test_every_patch_is_additive_if_not_exists():
    assert schema_patches._PATCHES, "a new column belongs here, not in a comment"
    for label, statement in schema_patches._PATCHES:
        sql = " ".join(statement.lower().split())
        assert sql.startswith("alter table "), label
        assert "add column if not exists" in sql, label
        assert "drop " not in sql, label
        assert "rename " not in sql, label


def test_apply_emits_every_statement_on_every_boot():
    """Twice is the replica / restart case. IF NOT EXISTS makes the second a no-op."""
    executed: list[str] = []

    @contextmanager
    def begin():
        conn = MagicMock()

        def execute(statement, *args, **kwargs):  # noqa: ANN001, ARG001
            executed.append(getattr(statement, "text", str(statement)))

        conn.execute.side_effect = execute
        yield conn

    engine = MagicMock()
    engine.begin = begin

    schema_patches.apply(engine)
    schema_patches.apply(engine)

    statements = [sql for _label, sql in schema_patches._PATCHES]
    assert executed == statements * 2
