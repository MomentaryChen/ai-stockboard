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

from app.services import auth as auth_service
from app.services import backtest_store
from app.services import chip as chip_service
from app.services import code_sync
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


def backtest_refresh(context: JobContext) -> JobResult:
    """Re-run the 四大買賣點 replay for every stock whose bars have moved.

    `skipped` here means every stored replay was already computed through its
    stock's newest bar -- the usual answer on a weekend, and the heartbeat that
    says the job is running rather than that it has nothing to maintain.

    Failures are counted rather than raised: each row is derived data that the
    endpoint recomputes on demand anyway, so one stock with a malformed bar
    must not cost the other few hundred their refresh.
    """
    stats = backtest_store.refresh_all(context.db, force=context.force)

    if stats["failed"]:
        status = "success" if stats["computed"] else "failed"
        message = f"{stats['failed']} stock(s) could not be replayed"
    elif stats["computed"]:
        status, message = "success", None
    else:
        status, message = "skipped", "Every backtest is current"

    return JobResult(status=status, message=message, stats=stats)


def chip_refresh(context: JobContext) -> JobResult:
    """Pull the last few sessions of T86 / MI_MARGN (and TPEX equivalents).

    `skipped` here means every requested session was already stamped fresh --
    the usual answer on a weekend, or when the market board has no calendar
    bars yet so there is nothing to date the reports with.
    """
    stats = chip_service.refresh_recent(context.db, force=context.force)
    if stats["fetched"]:
        status, message = "success", None
    elif stats["cached"]:
        status, message = "skipped", "Every recent chip report is current"
    else:
        status, message = "skipped", "No trading calendar in daily_price yet"
    return JobResult(status=status, message=message, stats=stats)


def refresh_token_cleanup(context: JobContext) -> JobResult:
    """Delete refresh tokens that can no longer be used for anything.

    Expired rows are dead weight. Revoked ones are not: they are kept for
    `auth_service.REVOKED_RETENTION_DAYS` so replaying a rotated token still
    matches a row and trips reuse detection instead of looking like a token we
    never issued. Only rows past that window are removed here.

    Deleting nothing is a `success`, which is where this handler parts company
    with the three above: they skip because they decided the work was not worth
    doing yet, while this one has no such short circuit -- it always runs the
    sweep, and a count of zero means the table was already clean, i.e. the job
    finished its work. Calling that `skipped` froze `last_success_at` (see
    `jobs/store.py`, which counts successes only) on any instance quiet enough
    to expire no tokens between runs, so the console's staleness rule -- two
    missed cycles -- flagged a job that had in fact run correctly every night.
    """
    deleted = auth_service.purge_dead_refresh_tokens(context.db)
    return JobResult(
        status="success",
        message=None if deleted else "No dead tokens to clear",
        stats={"deleted": deleted},
    )
