"""Executing one job, exactly once at a time, and writing down what happened.

Three things are enforced here rather than in the router, because they must
hold however the job was started -- scheduler, admin button, or a future
webhook:

  * **One at a time.** Each job has a non-blocking lock. A second attempt while
    one is in flight is refused, not queued: the listing sync holds an
    exchange rate limiter for ~40 s, and two concurrent scrapes are how you get
    the service's IP banned.
  * **A cooldown on manual runs.** Bounded by the job definition and measured
    from the database, so a restart does not reset it.
  * **Every attempt is recorded.** Including the ones that raise -- a handler
    that blew up leaves a `failed` row with the exception in `message`, which
    is exactly the case an operator most needs to see afterwards.

The lock is per process. A second replica can still run the same job at the
same moment; that is deliberate and unchanged from the old scheduler, and every
handler here is idempotent.
"""

import datetime
import logging
import threading

from app.db import SessionLocal
from app.models import JobRun
from app.services.jobs import store
from app.services.jobs.registry import JobContext, JobDefinition, JobResult

logger = logging.getLogger(__name__)

TRIGGER_STARTUP = "startup"
TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class JobBusyError(RuntimeError):
    """The job is already running. Becomes a 409."""


class CooldownError(RuntimeError):
    """Manual runs are being asked for too fast. Becomes a 429.

    Carries the wait so the response can say how long, rather than making the
    operator guess and try again.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"This job was run manually a moment ago; retry in "
            f"{retry_after_seconds}s"
        )
        self.retry_after_seconds = retry_after_seconds


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _lock_for(job_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(job_id, threading.Lock())


def is_running(job_id: str) -> bool:
    return _lock_for(job_id).locked()


def run_job(
    job: JobDefinition,
    *,
    trigger: str,
    actor: str | None = None,
    force: bool = False,
) -> JobRun:
    """Run `job` in the calling thread and return the recorded attempt.

    Raises `JobBusyError` if it is already in flight. Never raises whatever the
    handler raised: that is caught, logged, and stored as a failed run.
    """
    lock = _lock_for(job.id)
    if not lock.acquire(blocking=False):
        raise JobBusyError(job.id)

    started_at = _now()
    try:
        with SessionLocal() as db:
            state = store.get_schedule(db, job)
            context = JobContext(
                db=db,
                trigger=trigger,
                force=force,
                freshness_seconds=state.freshness_seconds,
            )
            try:
                result = job.handler(context)
            except Exception as exc:
                # The handler may have left a transaction half open; the run
                # row has to be written on a clean session.
                db.rollback()
                logger.exception("job %s raised", job.id)
                result = JobResult(
                    status="failed", message=f"{exc.__class__.__name__}: {exc}"
                )

            record = store.record_run(
                db,
                job_id=job.id,
                started_at=started_at,
                finished_at=_now(),
                result=result,
                trigger=trigger,
                actor=actor,
            )
    finally:
        lock.release()

    logger.info(
        "job %s %s (trigger=%s%s) in %.1fs%s",
        job.id,
        record.status,
        trigger,
        f", by {actor}" if actor else "",
        (record.finished_at - record.started_at).total_seconds(),
        f" -- {record.message}" if record.message else "",
    )
    return record


def check_manual_allowed(job: JobDefinition) -> None:
    """Raise if a manual run would breach the cooldown or collide with a run.

    Split out from `trigger_manual` so the admin API can refuse *before*
    starting a background thread, and answer with a status code that says which
    of the two it was.
    """
    if is_running(job.id):
        raise JobBusyError(job.id)

    if job.manual_cooldown_seconds <= 0:
        return

    with SessionLocal() as db:
        previous = store.last_manual_run_at(db, job.id)
    if previous is None:
        return

    elapsed = (_now() - previous).total_seconds()
    remaining = job.manual_cooldown_seconds - elapsed
    if remaining > 0:
        raise CooldownError(int(remaining) + 1)


def trigger_manual(job: JobDefinition, actor: str) -> None:
    """Start `job` on a background thread on an operator's behalf.

    Deliberately not awaited: the listing sync takes the better part of a
    minute, and an admin page that lists several jobs cannot hold a request
    open for each one. The run appears in the audit trail as soon as it
    finishes, and the page polls.
    """
    check_manual_allowed(job)

    def _run() -> None:
        try:
            run_job(job, trigger=TRIGGER_MANUAL, actor=actor, force=True)
        except JobBusyError:
            # Lost the race with the scheduler between the check above and
            # here. Nothing to do -- a run of this job is happening either way.
            logger.info("manual run of %s skipped: already running", job.id)
        except Exception:
            logger.exception("manual run of %s could not be recorded", job.id)

    threading.Thread(target=_run, name=f"job-{job.id}-manual", daemon=True).start()
