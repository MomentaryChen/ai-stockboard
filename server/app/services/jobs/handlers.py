"""What each background job actually does.

Handlers are thin on purpose: the work belongs to the service that owns the
table (`code_sync` owns `stock_code`, `auth` owns `refresh_token`), and this
module only translates that service's own report into a `JobResult`. Keeping it
that way is what lets the same code be called from a route, a test or a REPL
without a scheduler anywhere in sight.

A handler must not record anything. `runner.run_job` writes the `job_run` row
around it, so a handler that raises is logged just as thoroughly as one that
returns -- which is not true of anything that logs its own success.
"""

import logging

from app.services import code_sync
from app.services import auth as auth_service
from app.services.jobs.registry import JobContext, JobResult

logger = logging.getLogger(__name__)


def stock_code_sync(context: JobContext) -> JobResult:
    """Reconcile `stock_code` with the exchanges' ISIN registry.

    `skipped` here means the listing was reconciled recently enough that this
    attempt had nothing to do -- the usual answer when the container restarts
    an hour after a good run.
    """
    report = code_sync.run(
        context.db,
        force=context.force,
        min_age_seconds=context.freshness_seconds,
    )
    # code_sync says "synced"; the job tables say "success". One vocabulary at
    # the boundary, so the admin UI can label every job the same way.
    status = "success" if report.status == "synced" else report.status
    return JobResult(status=status, message=report.message, stats=report.as_stats())


def refresh_token_cleanup(context: JobContext) -> JobResult:
    """Delete refresh tokens that can no longer be used for anything.

    Expired rows are dead weight. Revoked ones are not: they are kept for
    `auth_service.REVOKED_RETENTION_DAYS` so replaying a rotated token still
    matches a row and trips reuse detection instead of looking like a token we
    never issued. Only rows past that window are removed here.
    """
    deleted = auth_service.purge_dead_refresh_tokens(context.db)
    return JobResult(
        status="success" if deleted else "skipped",
        message=None if deleted else "No expired tokens to clear",
        stats={"deleted": deleted},
    )
