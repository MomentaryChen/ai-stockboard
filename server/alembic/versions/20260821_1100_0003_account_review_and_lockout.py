"""account review queue and per-account login lockout

Revision ID: 0003_account_review_and_lockout
Revises: 0002_retire_create_all_leftovers
Create Date: 2026-08-21 11:00:00.000000+00:00

Three columns on `app_user`, all additive and all defaulted, so this applies to
a table that already holds accounts without touching a single existing row's
behaviour:

  * `pending_approval` -- raised by self-service registration and cleared by
    the ADMIN who activates the account. It exists next to `is_active` because
    that flag alone cannot tell an account waiting for its first review from
    one an admin suspended, and the two need different words in the UI and a
    different action from the operator.
  * `failed_login_count` / `locked_until` -- consecutive failed sign-ins, and
    when the account starts answering again. In the database rather than in
    process memory because a lockout a restart clears is a lockout the attacker
    can clear, and restarting a container is not a privileged operation for
    whoever is already grinding the login endpoint.

`pending_approval` defaults to false, which is the load-bearing part: every
account that already exists is one the operator is living with, not one waiting
in a queue nobody knew existed. Defaulting it the other way would drop the
entire existing user base into the review page and lock them all out at once.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_account_review_and_lockout"
down_revision: str | None = "0002_retire_create_all_leftovers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table app_user "
        "add column if not exists pending_approval boolean default false not null"
    )
    op.execute(
        "alter table app_user "
        "add column if not exists failed_login_count integer default 0 not null"
    )
    op.execute("alter table app_user add column if not exists locked_until timestamptz")


def downgrade() -> None:
    # Dropping these loses the review queue: an account sitting at
    # pending_approval becomes an ordinary deactivated one, indistinguishable
    # from a suspension. That is recoverable by hand and the alternative --
    # refusing to downgrade -- helps nobody.
    op.execute("alter table app_user drop column if exists locked_until")
    op.execute("alter table app_user drop column if exists failed_login_count")
    op.execute("alter table app_user drop column if exists pending_approval")
