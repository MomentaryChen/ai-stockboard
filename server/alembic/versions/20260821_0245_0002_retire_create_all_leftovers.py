"""retire the objects create_all could not remove

Revision ID: 0002_retire_create_all_leftovers
Revises: 0001_baseline
Create Date: 2026-08-21 02:45:00.000000+00:00

Two things were left behind in every long-lived database, because dropping is
the one thing `create_all` has never been able to do:

  * `stock_code_sync_run` -- the table the listing sync owned before jobs were
    generalised into `job_run`. Its rows are the only record of how that job
    behaved before the change, so they are copied across before the table goes.
    The copy used to happen on every single boot, in
    `job_store.backfill_legacy_sync_runs`, guarded on the target being empty;
    that function said in its own docstring that it existed because the project
    had no migration tool. It does now, so this revision is where it belongs
    and the boot-time version is deleted along with the table.
  * `ix_dividend_event_sid_ex_date` -- an index on exactly the columns of
    `dividend_event`'s primary key. It was dropped from the model and stayed in
    the database, costing a write on every dividend row for nothing.

Both steps check what is actually there first, so this is a no-op on a database
created from `0001_baseline` and safe to re-run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_retire_create_all_leftovers"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 'synced' was the old success status; the rest of the values already line up
# with what `job_run` expects. The per-job counters become the JSONB `stats`
# blob the admin table renders.
_BACKFILL = """
insert into job_run
    (job_id, started_at, finished_at, status, trigger, actor, stats, message)
select
    'stock_code_sync',
    started_at,
    finished_at,
    case when status = 'synced' then 'success' else status end,
    trigger,
    null,
    jsonb_build_object(
        'active', active, 'inserted', inserted, 'updated', updated,
        'delisted', delisted, 'pruned', pruned
    ),
    message
from stock_code_sync_run
order by started_at
"""


def upgrade() -> None:
    bind = op.get_bind()

    if "stock_code_sync_run" in sa.inspect(bind).get_table_names():
        # The boot-time backfill this replaces may already have run against
        # this database. It used the same guard, so re-checking it here is what
        # keeps the history from being copied in twice.
        already = bind.execute(
            sa.text("select count(*) from job_run where job_id = 'stock_code_sync'")
        ).scalar_one()
        if not already:
            op.execute(_BACKFILL)

        op.execute("drop table stock_code_sync_run")

    op.execute("drop index if exists ix_dividend_event_sid_ex_date")


def downgrade() -> None:
    # The index is reproducible; the table is not. Its rows now live in
    # `job_run` under job_id 'stock_code_sync' and are not moved back -- doing
    # so would have to guess which of them this revision put there.
    op.execute(
        "create index if not exists ix_dividend_event_sid_ex_date"
        " on dividend_event (sid, ex_date)"
    )
