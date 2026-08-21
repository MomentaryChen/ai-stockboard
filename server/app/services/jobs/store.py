"""Reading and writing the two job tables.

Schedules are read on every scheduler tick and on every admin request, so the
"no row yet" case has to be as ordinary as the rest: `get_schedule` folds the
job's coded defaults and any saved row into one `ScheduleState`, and nothing
downstream needs to know which it got.

Validation lives here rather than in the router because it is a rule about the
job, not about HTTP: an interval below the job's floor is refused whoever asks
and however they ask.
"""

import datetime
import logging
import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    MAX_JOB_RUNS_KEPT,
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    SCHEDULE_KINDS,
    JobRun,
    JobSchedule,
)
from app.services.jobs.registry import JobDefinition, JobResult

logger = logging.getLogger(__name__)
settings = get_settings()

# A failed run is retried on this cadence instead of waiting out the full
# schedule -- otherwise one unreachable registry costs a whole day.
RETRY_SECONDS = 600

_DAILY_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# A handler may skip work it decides is still fresh. The window is a little
# under the cadence so ordinary jitter -- a run that started 30 s late -- does
# not make the next one skip its whole slot.
FRESHNESS_RATIO = 0.9


class ScheduleError(ValueError):
    """A schedule an operator is not allowed to save. Becomes a 400."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


def _zone() -> datetime.tzinfo:
    """The wall clock `daily_at` is read in.

    Falls back to UTC rather than raising: a typo'd timezone must not stop
    every job in the service from being scheduled.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.scheduler_timezone)
    except Exception:
        logger.warning(
            "SCHEDULER_TIMEZONE=%r is not a known timezone -- daily jobs will "
            "fire on UTC instead",
            settings.scheduler_timezone,
        )
        return datetime.timezone.utc


class ScheduleState:
    """One job's effective schedule: its defaults, with any saved row on top."""

    def __init__(self, job: JobDefinition, row: JobSchedule | None) -> None:
        self.job_id = job.id
        self.is_default = row is None
        self.enabled = job.default_enabled if row is None else row.enabled
        self.kind = job.default_kind if row is None else row.kind
        self.interval_minutes = (
            job.default_interval_minutes if row is None else row.interval_minutes
        )
        self.daily_at = job.default_daily_at if row is None else row.daily_at
        self.updated_at = None if row is None else _as_utc(row.updated_at)
        self.updated_by = None if row is None else row.updated_by

    @property
    def cadence_seconds(self) -> float:
        if self.kind == SCHEDULE_DAILY:
            return 24 * 3600
        return self.interval_minutes * 60

    @property
    def freshness_seconds(self) -> float:
        return self.cadence_seconds * FRESHNESS_RATIO


class LastRun:
    """The single newest attempt, which is all the scheduler needs."""

    def __init__(self, row: JobRun) -> None:
        self.started_at = _as_utc(row.started_at)
        self.status = row.status


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------


def get_schedule(db: Session, job: JobDefinition) -> ScheduleState:
    return ScheduleState(job, db.get(JobSchedule, job.id))


def get_schedules(db: Session, jobs: list[JobDefinition]) -> dict[str, ScheduleState]:
    """Every job's schedule in one query -- the admin list asks for all of them."""
    rows = {row.job_id: row for row in db.execute(select(JobSchedule)).scalars()}
    return {job.id: ScheduleState(job, rows.get(job.id)) for job in jobs}


def _validate(job: JobDefinition, kind: str, interval_minutes: int, daily_at: str) -> None:
    if kind not in SCHEDULE_KINDS:
        raise ScheduleError(f"Unknown schedule kind '{kind}'")

    if kind == SCHEDULE_INTERVAL:
        if not job.min_interval_minutes <= interval_minutes <= job.max_interval_minutes:
            raise ScheduleError(
                f"Interval must be between {job.min_interval_minutes} and "
                f"{job.max_interval_minutes} minutes"
            )
    elif not _DAILY_AT.match(daily_at or ""):
        raise ScheduleError("Daily time must be HH:MM on a 24-hour clock")


def set_schedule(
    db: Session,
    job: JobDefinition,
    *,
    actor: str,
    enabled: bool | None = None,
    kind: str | None = None,
    interval_minutes: int | None = None,
    daily_at: str | None = None,
) -> ScheduleState:
    """Save a partial edit on top of the job's effective schedule.

    Validated against the *merged* result, not the submitted fields: switching
    a job to `interval` without sending an interval has to be checked against
    whatever interval it would then inherit.
    """
    current = get_schedule(db, job)

    merged_kind = kind if kind is not None else current.kind
    merged_interval = (
        interval_minutes if interval_minutes is not None else current.interval_minutes
    )
    merged_daily = daily_at if daily_at is not None else current.daily_at
    _validate(job, merged_kind, merged_interval, merged_daily)

    row = db.get(JobSchedule, job.id)
    if row is None:
        row = JobSchedule(job_id=job.id)
        db.add(row)

    row.enabled = current.enabled if enabled is None else enabled
    row.kind = merged_kind
    row.interval_minutes = merged_interval
    row.daily_at = merged_daily
    row.updated_by = actor[:32]
    db.commit()
    db.refresh(row)

    logger.info(
        "job %s schedule changed by %s: enabled=%s kind=%s interval=%dm daily_at=%s",
        job.id,
        actor,
        row.enabled,
        row.kind,
        row.interval_minutes,
        row.daily_at,
    )
    return ScheduleState(job, row)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def record_run(
    db: Session,
    *,
    job_id: str,
    started_at: datetime.datetime,
    finished_at: datetime.datetime,
    result: JobResult,
    trigger: str,
    actor: str | None,
) -> JobRun:
    """Append this attempt to the audit trail and trim the trail for this job."""
    row = JobRun(
        job_id=job_id,
        started_at=started_at,
        finished_at=finished_at,
        status=result.status,
        trigger=trigger,
        actor=actor[:32] if actor else None,
        stats={key: int(value) for key, value in result.stats.items()},
        message=result.message[:255] if result.message else None,
    )
    db.add(row)
    db.flush()  # the row needs an id before it can be counted out of the window

    # Keep the newest MAX_JOB_RUNS_KEPT *for this job*. Comparing on the id of
    # the oldest survivor is one statement, and ids are monotonic here.
    cutoff = db.execute(
        select(JobRun.id)
        .where(JobRun.job_id == job_id)
        .order_by(JobRun.id.desc())
        .offset(MAX_JOB_RUNS_KEPT)
        .limit(1)
    ).scalar()
    if cutoff is not None:
        db.execute(delete(JobRun).where(JobRun.job_id == job_id, JobRun.id <= cutoff))

    db.commit()
    db.refresh(row)
    return row


def list_runs(db: Session, job_id: str, limit: int = 50) -> tuple[int, list[JobRun]]:
    """Most recent attempts first, plus how many are on record."""
    total = count_runs(db, job_id)
    rows = db.execute(
        select(JobRun)
        .where(JobRun.job_id == job_id)
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    ).scalars()
    return total, list(rows)


def last_run(db: Session, job_id: str) -> JobRun | None:
    return (
        db.execute(
            select(JobRun)
            .where(JobRun.job_id == job_id)
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def last_runs(db: Session, job_ids: list[str]) -> dict[str, JobRun]:
    """The newest attempt for each job, in one pass.

    DISTINCT ON is PostgreSQL-specific and this service is PostgreSQL-only; the
    alternative is one query per job on a page that lists all of them.
    """
    rows = db.execute(
        select(JobRun)
        .where(JobRun.job_id.in_(job_ids))
        .order_by(JobRun.job_id, JobRun.started_at.desc())
        .distinct(JobRun.job_id)
    ).scalars()
    return {row.job_id: row for row in rows}


def last_success_at(db: Session, job_id: str) -> datetime.datetime | None:
    """Start time of the newest run that actually did the work.

    Skipped runs deliberately do not count: they prove the scheduler is alive,
    not that the job's output is current.
    """
    value = db.execute(
        select(func.max(JobRun.started_at)).where(
            JobRun.job_id == job_id, JobRun.status == "success"
        )
    ).scalar()
    return _as_utc(value) if value is not None else None


def last_success_map(db: Session, job_ids: list[str]) -> dict[str, datetime.datetime]:
    rows = db.execute(
        select(JobRun.job_id, func.max(JobRun.started_at))
        .where(JobRun.job_id.in_(job_ids), JobRun.status == "success")
        .group_by(JobRun.job_id)
    ).all()
    return {job_id: _as_utc(value) for job_id, value in rows if value is not None}


def last_manual_run_at(db: Session, job_id: str) -> datetime.datetime | None:
    """Backs the run-now cooldown.

    Read from the database rather than from process memory, so restarting the
    container -- or asking a second replica instead -- cannot be used to get
    around it.
    """
    value = db.execute(
        select(func.max(JobRun.started_at)).where(
            JobRun.job_id == job_id, JobRun.trigger == "manual"
        )
    ).scalar()
    return _as_utc(value) if value is not None else None


def count_runs_map(db: Session, job_ids: list[str]) -> dict[str, int]:
    rows = db.execute(
        select(JobRun.job_id, func.count())
        .where(JobRun.job_id.in_(job_ids))
        .group_by(JobRun.job_id)
    ).all()
    return dict(rows)


def count_runs(db: Session, job_id: str) -> int:
    return db.execute(
        select(func.count()).select_from(JobRun).where(JobRun.job_id == job_id)
    ).scalar_one()


# --------------------------------------------------------------------------
# When the next fire is due
# --------------------------------------------------------------------------


def _next_daily(daily_at: str, now: datetime.datetime) -> datetime.datetime:
    hour, minute = (int(part) for part in daily_at.split(":"))
    local = now.astimezone(_zone())
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += datetime.timedelta(days=1)
    return candidate.astimezone(datetime.timezone.utc)


def next_run_at(
    state: ScheduleState,
    last: LastRun | None,
    now: datetime.datetime | None = None,
) -> datetime.datetime | None:
    """When this job should fire next, or None when it is switched off.

    A job that has never run is due immediately; a job whose last attempt
    failed is retried on the short cadence rather than at its next slot, so one
    unreachable upstream does not cost a whole day. Both the scheduler thread
    and the admin API call this, which is what makes the time the operator is
    shown the same one the thread is actually sleeping on.
    """
    if not state.enabled:
        return None

    now = now or _now()
    if last is None:
        return now
    if last.status == "failed":
        return last.started_at + datetime.timedelta(seconds=RETRY_SECONDS)
    if state.kind == SCHEDULE_DAILY:
        return _next_daily(state.daily_at, now)
    return last.started_at + datetime.timedelta(minutes=state.interval_minutes)
