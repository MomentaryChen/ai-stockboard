"""What `/api/health` knows beyond "the database answered".

The point of this module is stated in README's known limitations: the batch
jobs and the nightly `pg_dump` both fail *silently*. A listing sync that has
been broken for a fortnight and one that had nothing to do leave the same
trace, and nothing anywhere raises a hand. There is no alerting stack here and
adding one is a separate piece of work -- so instead the facts an operator
would want an alert about are folded into the endpoint an uptime monitor is
already polling, and `status` goes to "degraded" when any of them is bad.

Deliberately cheap: two indexed queries and one directory listing, because a
monitor hits this every 30 seconds and a healthcheck every 10.
"""

import datetime
import logging
import os

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.jobs import registry, store

logger = logging.getLogger(__name__)
settings = get_settings()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def job_health(db: Session) -> tuple[list[str], datetime.datetime | None]:
    """Jobs whose most recent attempt failed, and when the newest failure was.

    Judged on the *latest* attempt only. A job that failed at 03:00 and
    succeeded on the 03:10 retry is working, and an operator paged about it
    would learn nothing they could act on.
    """
    jobs = registry.all_jobs()
    latest = store.last_runs(db, [job.id for job in jobs])

    failing = sorted(
        job_id for job_id, run in latest.items() if run.status == "failed"
    )
    newest = max(
        (latest[job_id].started_at for job_id in failing),
        default=None,
    )
    if newest is not None and newest.tzinfo is None:
        newest = newest.replace(tzinfo=datetime.timezone.utc)
    return failing, newest


def backup_health() -> tuple[str, datetime.datetime | None, float | None]:
    """Age of the newest verified dump: (status, taken_at, age_hours).

    Reads the directory the `db-backup` container writes into, mounted
    read-only (see deployment/docker-compose.yml). "unchecked" when
    BACKUP_STATUS_DIR is unset, which is the case for a server run outside
    Docker -- an unset path must not read as a missing backup.

    Only `*.dump` counts. backup.sh writes to `*.dump.partial` and renames on
    success precisely so an interrupted dump cannot be mistaken for a good one,
    and this has to make the same distinction or it undoes that.
    """
    directory = settings.backup_status_dir.strip()
    if not directory:
        return "unchecked", None, None

    try:
        newest = max(
            (
                entry.stat().st_mtime
                for entry in os.scandir(directory)
                if entry.is_file() and entry.name.endswith(".dump")
            ),
            default=None,
        )
    except OSError as exc:
        # A wrong path or a permission problem is an operator's problem, not a
        # 500: report it as "missing" and keep the endpoint answering.
        logger.warning("cannot read BACKUP_STATUS_DIR %r: %s", directory, exc)
        return "missing", None, None

    if newest is None:
        return "missing", None, None

    taken_at = datetime.datetime.fromtimestamp(newest, datetime.timezone.utc)
    age_hours = (_now() - taken_at).total_seconds() / 3600
    status = "stale" if age_hours > settings.backup_max_age_hours else "ok"
    return status, taken_at, round(age_hours, 1)


def alerts(
    *,
    database_ok: bool,
    failing_jobs: list[str],
    backup_status: str,
    backup_age_hours: float | None,
) -> list[str]:
    """One line per thing a human would want to be told about.

    English and human-readable rather than machine codes: the audience is
    whoever is looking at a monitor's alert body at 03:00, and an empty list is
    the machine-readable part.
    """
    out: list[str] = []
    if not database_ok:
        out.append("database is unreachable")
    if failing_jobs:
        out.append(f"background job(s) failing: {', '.join(failing_jobs)}")
    if backup_status == "missing":
        out.append("no database backup on disk")
    elif backup_status == "stale":
        out.append(
            f"newest database backup is {backup_age_hours:.0f}h old "
            f"(limit {settings.backup_max_age_hours}h)"
        )
    return out
