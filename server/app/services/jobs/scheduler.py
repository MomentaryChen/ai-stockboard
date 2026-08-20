"""The threads that fire jobs on their schedule.

One daemon thread per job rather than one thread walking a queue: the jobs
block for wildly different lengths (a 40-second scrape next to a 20 ms delete),
they are independent, and there are a handful of them. A shared thread would
mean the cleanup job waits out the sync's network call for no reason.

Daemon threads because everything underneath is blocking -- `requests`, psycopg,
the rate limiter -- and the first listing scrape takes the better part of a
minute, which must not hold up the port opening or the container healthcheck.

Each thread sleeps on an `Event` rather than `time.sleep`, which is what makes
an edit in the admin UI take effect immediately: saving a schedule calls
`notify()`, the thread wakes, re-reads the row and recomputes when it is next
due. Without that, shortening a 24-hour interval would not be felt until the
old 24 hours had elapsed.
"""

import datetime
import logging
import threading

from app.config import get_settings
from app.db import SessionLocal
from app.services.jobs import registry, runner, store
from app.services.jobs.registry import JobDefinition

logger = logging.getLogger(__name__)
settings = get_settings()

# How long a disabled job's thread parks before looking again. `notify()` wakes
# it the moment an admin switches it back on; this is only the backstop for a
# row changed by something other than this process (a second replica, psql).
DISABLED_POLL_SECONDS = 900

# Ceiling on a single sleep. A thread never parks longer than this without
# re-reading its schedule, which is what makes the loop self-correcting: a
# manual run, a row edited by another replica, or the clock jumping are all
# picked up within this window even though none of them calls `notify()`.
MAX_SLEEP_SECONDS = 900

_wakeups: dict[str, threading.Event] = {}
_stopping = threading.Event()
_started = False


def notify(job_id: str) -> None:
    """Wake `job_id`'s thread so it re-reads its schedule now."""
    event = _wakeups.get(job_id)
    if event is not None:
        event.set()


def is_running() -> bool:
    """Whether the scheduler threads are up in *this* process."""
    return _started


def _wait(event: threading.Event, seconds: float) -> bool:
    """Sleep, capped at MAX_SLEEP_SECONDS. True if woken early or shutting down."""
    if _stopping.is_set():
        return True
    return event.wait(min(max(0.0, seconds), MAX_SLEEP_SECONDS)) or _stopping.is_set()


def _loop(job: JobDefinition) -> None:
    event = _wakeups[job.id]
    # Only for jobs whose handler can tell by itself that there is nothing to
    # do; see JobDefinition.run_on_startup.
    startup_pending = job.run_on_startup

    while not _stopping.is_set():
        event.clear()

        try:
            with SessionLocal() as db:
                state = store.get_schedule(db, job)
                row = store.last_run(db, job.id)
                last = store.LastRun(row) if row is not None else None
        except Exception:
            # Almost always PostgreSQL not up yet. Back off and try again
            # rather than killing the thread, or the job stays dead until the
            # next restart.
            logger.exception("job %s: could not read its schedule", job.id)
            _wait(event, store.RETRY_SECONDS)
            continue

        if not state.enabled:
            startup_pending = False
            _wait(event, DISABLED_POLL_SECONDS)
            continue

        if startup_pending:
            delay = 0.0
        else:
            due = store.next_run_at(state, last)
            if due is None:  # switched off between the two reads above
                continue
            delay = (due - datetime.datetime.now(datetime.timezone.utc)).total_seconds()

        if delay > 0:
            if _wait(event, delay):
                continue  # woken by an edit or a shutdown -- recompute
            if delay > MAX_SLEEP_SECONDS:
                # Only slept a slice of the wait. Go round again rather than
                # firing early; the recompute costs one small query.
                continue

        if _stopping.is_set():
            break

        trigger = runner.TRIGGER_STARTUP if startup_pending else runner.TRIGGER_SCHEDULE
        startup_pending = False
        try:
            runner.run_job(job, trigger=trigger)
        except runner.JobBusyError:
            # An admin pressed run-now at the same moment. Their run is doing
            # the work; come back when it has had time to finish.
            _wait(event, min(job.expected_seconds * 2, 120))
        except Exception:
            logger.exception("job %s: scheduler tick failed", job.id)
            _wait(event, store.RETRY_SECONDS)


def start() -> None:
    """Spawn the scheduler threads. Safe to call once, at startup."""
    global _started

    registry.build()

    if not settings.jobs_scheduler_enabled:
        logger.warning(
            "JOBS_SCHEDULER_ENABLED is off -- no background job will fire on its "
            "own in this process; admins can still run them from /admin/jobs"
        )
        return

    if _started:
        return
    _started = True

    for job in registry.all_jobs():
        _wakeups[job.id] = threading.Event()
        threading.Thread(target=_loop, args=(job,), name=f"job-{job.id}", daemon=True).start()

    logger.info(
        "job scheduler started: %s",
        ", ".join(job.id for job in registry.all_jobs()),
    )


def stop() -> None:
    """Ask the threads to stop at their next wake-up. Used by tests."""
    _stopping.set()
    for event in _wakeups.values():
        event.set()
