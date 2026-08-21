"""system_setting: admin-overridable runtime knobs

Revision ID: 0008_system_setting
Revises: 0007_watchlist_groups
Create Date: 2026-08-21 11:00:00.000000+00:00

Mirrors `job_schedule`: the environment seeds the first-boot default, and an
admin edit at /admin/ai writes a row that survives container restarts. Without
a table the only way to change the active Gemini model would be to edit .env
and bounce the process, which is exactly the foot-gun the jobs page already
avoids for schedules.

`updated_by` is username text rather than a foreign key for the same reason
`job_schedule` uses text: an audit trail that still reads after the account is
deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_system_setting"
down_revision: str | None = "0007_watchlist_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_setting")
