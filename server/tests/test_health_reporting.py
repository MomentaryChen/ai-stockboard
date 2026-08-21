"""/api/health is the only thing in this deployment that notices a fault.

There is no alerting stack, so the endpoint an uptime monitor already polls has
to carry the two failures that are otherwise invisible: a background job that
has been failing for a fortnight, and a nightly `pg_dump` that stopped running.
Both look exactly like "nothing happened" from outside.
"""

import datetime
import json
import logging
import os

import pytest

from app.logging_config import JsonFormatter, set_request_id
from app.services import health


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """Point the backup check at an empty temporary directory."""
    monkeypatch.setattr(health.settings, "backup_status_dir", str(tmp_path))
    monkeypatch.setattr(health.settings, "backup_max_age_hours", 36)
    return tmp_path


def _dump(directory, name: str, age_hours: float):
    path = directory / name
    path.write_bytes(b"not really a dump")
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=age_hours
    )
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------


def test_unset_directory_is_not_a_missing_backup(monkeypatch) -> None:
    """A server running outside Docker has no backup directory to look at, and
    reporting that as a fault would make the endpoint cry wolf on every dev
    machine."""
    monkeypatch.setattr(health.settings, "backup_status_dir", "")

    assert health.backup_health() == ("unchecked", None, None)


def test_empty_directory_is_missing(backup_dir) -> None:
    status, taken_at, _age = health.backup_health()

    assert status == "missing"
    assert taken_at is None


def test_recent_dump_is_ok(backup_dir) -> None:
    _dump(backup_dir, "stockboard-20260821-040000.dump", age_hours=2)

    status, taken_at, age = health.backup_health()

    assert status == "ok"
    assert taken_at is not None
    assert 1.5 < age < 2.5


def test_old_dump_is_stale(backup_dir) -> None:
    _dump(backup_dir, "stockboard-20260818-040000.dump", age_hours=50)

    status, _taken_at, age = health.backup_health()

    assert status == "stale"
    assert age > 36


def test_partial_dumps_do_not_count(backup_dir) -> None:
    """backup.sh writes `*.dump.partial` and renames on success precisely so an
    interrupted dump cannot be mistaken for a good one. Counting it here would
    undo that, and the report would be wrong in the one direction that matters.
    """
    _dump(backup_dir, "stockboard-20260821-040000.dump.partial", age_hours=1)
    _dump(backup_dir, "stockboard-20260818-040000.dump", age_hours=50)

    status, _taken_at, _age = health.backup_health()

    assert status == "stale"


def test_newest_dump_wins(backup_dir) -> None:
    _dump(backup_dir, "stockboard-20260810-040000.dump", age_hours=200)
    _dump(backup_dir, "stockboard-20260821-040000.dump", age_hours=3)

    status, _taken_at, _age = health.backup_health()

    assert status == "ok"


def test_unreadable_directory_reports_rather_than_raises(monkeypatch) -> None:
    """A wrong path is an operator's mistake. It must not turn the health
    endpoint into a 500, because then the container healthcheck fails and the
    frontend never starts -- over a misconfigured backup path."""
    monkeypatch.setattr(
        health.settings, "backup_status_dir", "/nowhere/that/exists/at/all"
    )

    status, taken_at, age = health.backup_health()

    assert status == "missing"
    assert (taken_at, age) == (None, None)


# --------------------------------------------------------------------------
# Alert lines
# --------------------------------------------------------------------------


def test_healthy_deployment_raises_nothing() -> None:
    assert (
        health.alerts(
            database_ok=True,
            failing_jobs=[],
            backup_status="ok",
            backup_age_hours=4.0,
        )
        == []
    )


def test_unchecked_backup_is_not_an_alert() -> None:
    assert (
        health.alerts(
            database_ok=True,
            failing_jobs=[],
            backup_status="unchecked",
            backup_age_hours=None,
        )
        == []
    )


def test_each_fault_gets_its_own_line() -> None:
    lines = health.alerts(
        database_ok=False,
        failing_jobs=["stock_code_sync", "refresh_token_cleanup"],
        backup_status="stale",
        backup_age_hours=61.0,
    )

    assert len(lines) == 3
    joined = " | ".join(lines)
    assert "database" in joined
    # Naming the jobs is the point: "a job is failing" is not actionable.
    assert "stock_code_sync" in joined
    assert "refresh_token_cleanup" in joined
    assert "61h" in joined


# --------------------------------------------------------------------------
# JSON log format
# --------------------------------------------------------------------------


def test_json_formatter_emits_one_parseable_object() -> None:
    token = set_request_id("abc123")
    try:
        record = logging.LogRecord(
            "app.access", logging.INFO, __file__, 1, "GET /x -> %d", (200,), None
        )
        record.request_id = "abc123"
        record.ctx_status = 200
        payload = json.loads(JsonFormatter().format(record))
    finally:
        from app.logging_config import reset_request_id

        reset_request_id(token)

    assert payload["message"] == "GET /x -> 200"
    assert payload["request_id"] == "abc123"
    # ctx_-prefixed extras become their own fields rather than being baked into
    # the message, which is the whole reason for emitting JSON at all.
    assert payload["status"] == 200
    assert payload["level"] == "INFO"
