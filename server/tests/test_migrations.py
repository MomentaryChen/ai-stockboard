"""Alembic is the migration tool this project has.

`create_all` never ALTERs an existing table, which is why `schema_patches.py`
existed. That file is gone: the baseline revision absorbed its `ADD COLUMN IF
NOT EXISTS`, and `app.migrations.upgrade_to_head` is what every boot runs.
These tests pin the properties that made the old patches safe -- idempotent
DDL, `upgrade head` as a no-op when current -- so a rewrite that brings back
`create_all`, or edits the frozen baseline in place, fails here rather than
on the next `docker compose up`.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import migrations

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_revision(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _revisions() -> list:
    return [
        _load_revision(path)
        for path in sorted(_VERSIONS.glob("*.py"))
        if path.name != "script.py.mako"
    ]


def test_schema_patches_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.schema_patches")


def test_boot_migrates_to_head_instead_of_create_all():
    from app.main import lifespan

    source = inspect.getsource(lifespan)
    assert "upgrade_to_head" in source
    assert "create_all" not in source
    assert "schema_patches" not in source


def test_baseline_ddl_is_idempotent_if_not_exists():
    """Fresh *and* legacy databases share this revision; IF NOT EXISTS is why."""
    baseline = next(m for m in _revisions() if m.revision == "0001_baseline")
    statements = (*baseline._TABLES, *baseline._INDEXES, *baseline._COLUMNS)
    assert statements
    for statement in statements:
        sql = " ".join(statement.lower().split())
        assert "if not exists" in sql, statement
        assert "drop " not in sql, statement


def test_revision_graph_is_a_single_chain():
    modules = _revisions()
    by_id = {m.revision: m.down_revision for m in modules}
    assert len(by_id) == len(modules)

    roots = [rev for rev, down in by_id.items() if down is None]
    assert roots == ["0001_baseline"]

    children: dict[str | None, list[str]] = {}
    for rev, down in by_id.items():
        children.setdefault(down, []).append(rev)
    for parent, kids in children.items():
        assert len(kids) == 1, f"{parent} has branches {kids}"

    walked: list[str] = []
    current: str | None = None
    while current in children:
        nxt = children[current][0]
        walked.append(nxt)
        current = nxt
    assert set(walked) == set(by_id)


def test_upgrade_to_head_takes_the_advisory_lock_then_runs_alembic(monkeypatch):
    executed: list[str] = []
    upgraded: list[str] = []

    class _Lock:
        def execution_options(self, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, statement, params=None):  # noqa: ANN001, ARG002
            executed.append(getattr(statement, "text", str(statement)))

    engine = MagicMock()
    engine.connect.return_value = _Lock()
    monkeypatch.setattr(
        migrations.command, "upgrade", lambda _config, rev: upgraded.append(rev)
    )

    migrations.upgrade_to_head(engine)
    migrations.upgrade_to_head(engine)

    assert upgraded == ["head", "head"]
    locks = [sql for sql in executed if "pg_advisory_lock" in sql]
    unlocks = [sql for sql in executed if "pg_advisory_unlock" in sql]
    assert len(locks) == 2
    assert len(unlocks) == 2
