"""Background jobs: what they are, when they fire, and what they did.

Four modules, in dependency order:

  * `registry`  -- the closed set of jobs and the guard rails on each
  * `store`     -- the `job_schedule` / `job_run` tables
  * `runner`    -- run one job, once at a time, and record the attempt
  * `scheduler` -- the threads that decide when

The split exists so that "when a job runs" is configuration an admin owns,
while "how often it is *allowed* to run" stays code they cannot reach. See
`app/routers/jobs.py` for where that line is enforced over HTTP.
"""

from app.services.jobs import registry, runner, scheduler, store

__all__ = ["registry", "runner", "scheduler", "store"]
