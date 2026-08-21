"""watchlist groups: named folders on the existing 20-stock list

Revision ID: 0006_watchlist_groups
Revises: 0005_ai_analysis
Create Date: 2026-08-21 18:00:00.000000+00:00

One new table, `watchlist_group`, and a nullable `group_id` on `watchlist_item`.

Groups partition the same watchlist rather than adding lists: the realtime
endpoint already caps a quote request at 20 sids, and a second independent
list would blow that budget. Existing rows get `group_id` NULL, which is the
ungrouped state the UI already has to render, so this applies to a table that
already holds stocks without changing what anyone sees until they create a
group.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_watchlist_groups"
down_revision: str | None = "0005_ai_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_group",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_watchlist_group_user_name"),
    )
    op.create_index(
        "ix_watchlist_group_user_position",
        "watchlist_group",
        ["user_id", "position"],
        unique=False,
    )
    op.add_column(
        "watchlist_item",
        sa.Column("group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_watchlist_item_group_id",
        "watchlist_item",
        "watchlist_group",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_watchlist_item_group_id", "watchlist_item", type_="foreignkey"
    )
    op.drop_column("watchlist_item", "group_id")
    op.drop_index(
        "ix_watchlist_group_user_position", table_name="watchlist_group"
    )
    op.drop_table("watchlist_group")
