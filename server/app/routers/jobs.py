"""Background job administration. Every route here is ADMIN only.

This is the most privileged surface in the service: it lets an account make the
server reach out to the exchanges on demand and change when it does so
unattended. The rules that keep that from becoming a foot-gun are deliberately
spread across three layers, none of which trusts the one above it:

  * **Who** -- `require_admin` on the router itself, so a route added later
    cannot be published by accident. A USER gets 403 and a caller with no token
    gets 401, which is the split the frontend's refresh interceptor depends on.
  * **What** -- `store.set_schedule` validates the merged schedule against the
    job's own floor and ceiling. An interval below the floor is refused from an
    administrator exactly as it is from anyone else, because the limit protects
    TWSE's rate limit, not this service's permissions.
  * **How often** -- `runner.check_manual_allowed` refuses a second concurrent
    run (409) and a manual run inside the job's cooldown (429). The cooldown is
    measured off `job_run`, so restarting the process does not clear it.

Everything an operator does that changes behaviour is attributed: manual runs
carry `job_run.actor`, schedule edits carry `job_schedule.updated_by`, and both
are shown back in the UI.

Reads are unremarkable, but note that they stay behind the same guard: the run
log names internal hosts, upstream failure messages and admin usernames, none
of which belongs in an anonymous response. The public `/api/health` keeps its
own, much narrower, summary of the same job.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_admin
from app.models import AppUser, JobRun
from app.schemas import (
    JobListResponse,
    JobOut,
    JobRunOut,
    JobRunsResponse,
    JobScheduleOut,
    JobScheduleUpdateRequest,
    JobTriggerResponse,
)
from app.services.jobs import registry, runner, scheduler, store
from app.services.jobs.registry import JobDefinition

# The dependency is declared on the router, not per route: a new endpoint added
# below inherits the guard instead of relying on whoever adds it to remember.
router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin)],
)

settings = get_settings()


def _job_or_404(job_id: str) -> JobDefinition:
    """Resolve a job id against the registry.

    The registry is a closed set defined in code, which is also what stops a
    path parameter from reaching the database as an arbitrary key.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


def _run_out(row: JobRun) -> JobRunOut:
    return JobRunOut(
        id=row.id,
        job_id=row.job_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_seconds=round((row.finished_at - row.started_at).total_seconds(), 1),
        status=row.status,
        trigger=row.trigger,
        actor=row.actor,
        stats=row.stats or {},
        message=row.message,
    )


def _schedule_out(job: JobDefinition, state: store.ScheduleState) -> JobScheduleOut:
    return JobScheduleOut(
        enabled=state.enabled,
        kind=state.kind,
        interval_minutes=state.interval_minutes,
        daily_at=state.daily_at,
        timezone=settings.scheduler_timezone,
        min_interval_minutes=job.min_interval_minutes,
        max_interval_minutes=job.max_interval_minutes,
        is_default=state.is_default,
        updated_at=state.updated_at,
        updated_by=state.updated_by,
    )


def _job_out(
    job: JobDefinition,
    state: store.ScheduleState,
    last: JobRun | None,
    last_success_at: datetime.datetime | None,
    total_runs: int,
) -> JobOut:
    return JobOut(
        id=job.id,
        name=job.name,
        description=job.description,
        schedule=_schedule_out(job, state),
        running=runner.is_running(job.id),
        expected_seconds=job.expected_seconds,
        manual_cooldown_seconds=job.manual_cooldown_seconds,
        stat_labels=job.stat_labels,
        last_run=_run_out(last) if last is not None else None,
        last_success_at=last_success_at,
        # The same call the scheduler thread sleeps on, so the time shown is
        # the time that will actually happen -- including the shortened retry
        # after a failure.
        next_run_at=store.next_run_at(
            state, store.LastRun(last) if last is not None else None
        ),
        total_runs=total_runs,
    )


@router.get("", response_model=JobListResponse)
def list_jobs(db: Session = Depends(get_db)) -> JobListResponse:
    """Every job at once -- the console's landing view.

    Four queries for the whole page regardless of how many jobs exist: the
    schedules, the newest run each, the newest success each, and the counts.
    """
    jobs = registry.all_jobs()
    ids = [job.id for job in jobs]

    schedules = store.get_schedules(db, jobs)
    last_runs = store.last_runs(db, ids)
    successes = store.last_success_map(db, ids)
    totals = store.count_runs_map(db, ids)

    return JobListResponse(
        scheduler_enabled=scheduler.is_running(),
        timezone=settings.scheduler_timezone,
        jobs=[
            _job_out(
                job,
                schedules[job.id],
                last_runs.get(job.id),
                successes.get(job.id),
                totals.get(job.id, 0),
            )
            for job in jobs
        ],
    )


@router.get("/{job_id}/runs", response_model=JobRunsResponse)
def list_job_runs(
    job_id: str,
    limit: int = Query(50, ge=1, le=200, description="最多回傳幾筆執行紀錄"),
    db: Session = Depends(get_db),
) -> JobRunsResponse:
    """One job's audit trail, newest first, with the job itself for context.

    The job is included because a bare log is ambiguous: a run table that has
    been quiet for a week looks the same whether the job is broken or simply
    switched off.
    """
    job = _job_or_404(job_id)
    total, rows = store.list_runs(db, job.id, limit=limit)
    state = store.get_schedule(db, job)
    last = rows[0] if rows else None

    return JobRunsResponse(
        job=_job_out(job, state, last, store.last_success_at(db, job.id), total),
        total=total,
        runs=[_run_out(row) for row in rows],
    )


@router.patch("/{job_id}/schedule", response_model=JobOut)
def update_job_schedule(
    job_id: str,
    payload: JobScheduleUpdateRequest,
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    """Change when a job fires.

    Saving is what makes the `job_schedule` row authoritative over the
    environment variables that seeded it -- a schedule set here survives a
    container restart, which is the whole reason it is not just an env var.

    The change takes effect at once: `scheduler.notify` wakes the job's thread
    so it re-reads the row instead of finishing the sleep it had already
    started, which for a daily job would otherwise be up to 24 hours away.
    """
    job = _job_or_404(job_id)
    try:
        state = store.set_schedule(
            db,
            job,
            actor=admin.username,
            enabled=payload.enabled,
            kind=payload.kind,
            interval_minutes=payload.interval_minutes,
            daily_at=payload.daily_at,
        )
    except store.ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scheduler.notify(job.id)

    last = store.last_run(db, job.id)
    return _job_out(
        job, state, last, store.last_success_at(db, job.id), store.count_runs(db, job.id)
    )


@router.post("/{job_id}/run", response_model=JobTriggerResponse, status_code=202)
def run_job_now(
    job_id: str,
    admin: AppUser = Depends(require_admin),
) -> JobTriggerResponse:
    """Run a job now, on the operator's authority and under their name.

    202, not 200: the work happens on a background thread and the caller polls
    the run log for the outcome. Blocking here would hold a request open for
    the length of a 40-second scrape, per job, on a page that lists all of them.

    Refused with 409 if that job is already in flight, and 429 inside its
    cooldown -- see the module docstring for why both are enforced here rather
    than left to the UI's disabled button.
    """
    job = _job_or_404(job_id)
    try:
        runner.trigger_manual(job, actor=admin.username)
    except runner.JobBusyError as exc:
        raise HTTPException(status_code=409, detail="This job is already running") from exc
    except runner.CooldownError as exc:
        # Retry-After is the standard way to say "later": the UI reads the
        # detail string, but a script gets the machine-readable answer too.
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    return JobTriggerResponse(
        job_id=job.id,
        started=True,
        message=f"Started; expect roughly {job.expected_seconds}s",
    )
