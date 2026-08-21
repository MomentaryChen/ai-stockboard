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
from app.services import codes as codes_service
from app.services import dividend as dividend_service
from app.services import fundamentals as fundamentals_service
from app.services import valuation as valuation_service
from app.config import get_settings
from app.services.jobs.registry import JobContext, JobResult

logger = logging.getLogger(__name__)
settings = get_settings()

#: Calendar years of TWSE dividend history the warmup keeps. One more than
#: `hold_features.WINDOW_YEARS` so a ten-year payout streak is bounded by the
#: company's record rather than by the edge of what was fetched.
DIVIDEND_WARMUP_YEARS = 11


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


def dividend_board_warmup(context: JobContext) -> JobResult:
    """Pull the board-wide dividend archive so the 存股 lane is not lazy-only.

    One TWSE request covers every listed name for a calendar year, so warming
    the last decade here is a handful of calls that spares the first visitor to
    each cold stock the same work on the shared limiter -- and spares a
    board-level screen from queueing it per row.

    `skipped` means every bucket was already stamped fresh: past years are
    immutable and never re-fetched, so on any day after the first run of the
    year only the current year and the TPEX window can have anything to do.
    """
    stats = dividend_service.warm_board(
        context.db, years=DIVIDEND_WARMUP_YEARS, force=context.force
    )
    if stats["fetched"]:
        status, message = "success", None
    else:
        status, message = "skipped", "Every dividend bucket is current"
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


def fundamentals_refresh(context: JobContext) -> JobResult:
    """Pull the exchanges' valuation board and their current annual EPS.

    Four requests, all keyless: BWIBBU and the TPEx equivalent for PE / PBR /
    yield, then `t187ap14` from each exchange for the annual figure.

    `skipped` here means every bucket was already stamped fresh. Expect it on
    most days: the valuation snapshot expires daily but the EPS report only
    changes when a new quarter lands, and only its Q4 rows are ever written.
    """
    valuation_stats = valuation_service.refresh(context.db, force=context.force)
    eps_stats = fundamentals_service.refresh_exchange(context.db, force=context.force)

    stats = {
        "valuations": valuation_stats["rows"],
        "annuals": eps_stats["rows"],
        "fetched": valuation_stats["fetched"] + eps_stats["fetched"],
        "cached": valuation_stats["cached"] + eps_stats["cached"],
    }
    if stats["fetched"]:
        status, message = "success", None
    else:
        status, message = "skipped", "Every valuation and EPS bucket is current"
    return JobResult(status=status, message=message, stats=stats)


def fundamentals_backfill(context: JobContext) -> JobResult:
    """Fill the decade of annual EPS and ROE the exchanges do not publish.

    Works through the listing a slice at a time and stamps each company as it
    goes, so the board fills over a week of nightly runs instead of one sweep
    that would exhaust an hourly quota halfway and leave no record of where it
    stopped.

    `skipped` means every listed company already carries a stamp -- the steady
    state once the sweep has finished, and the heartbeat that says the job is
    alive rather than that it has work it is failing to do.
    """
    sids = [info.code for info in codes_service.all_stocks()]
    stats = fundamentals_service.backfill_history(
        context.db,
        sids,
        years=settings.fundamentals_history_years,
        limit=settings.finmind_backfill_batch,
    )

    if stats["stocks"]:
        # Partial failures are counted, not raised: one delisted code must not
        # cost the rest of the batch its turn.
        status = "success"
        message = (
            f"{stats['failed']} company(ies) could not be fetched"
            if stats["failed"]
            else None
        )
    elif stats["failed"]:
        status, message = "failed", "Every company in this batch failed"
    else:
        status, message = "skipped", "Every listed company already has history"
    return JobResult(status=status, message=message, stats=stats)
