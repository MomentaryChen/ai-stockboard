"""engine_run and engine_recommendation: the daily sector batch's output

Revision ID: 0013_engine_recommendation
Revises: 0012_ai_hold_depth
Create Date: 2026-10-30 18:00:00.000000+00:00

Rows are kept per day rather than overwritten. Checking later whether these
calls were any good needs what was said at the time, and that cannot be
recomputed: the listing, the rankings and Yahoo's adjusted prices all move.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013_engine_recommendation"
down_revision: str | None = "0012_ai_hold_depth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_run",
        sa.Column("as_of", sa.Date(), primary_key=True),
        sa.Column("engine_version", sa.String(length=16), primary_key=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("hold_backtest", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        "engine_recommendation",
        sa.Column("as_of", sa.Date(), primary_key=True),
        sa.Column("engine_version", sa.String(length=16), primary_key=True),
        sa.Column("sid", sa.String(length=16), primary_key=True),
        sa.Column("sector", sa.String(length=32), nullable=False),
        sa.Column("sector_rank", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("hold_state", sa.String(length=12), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
    )
    # "This stock's latest call" is the stock page's one query against this table.
    op.create_index("ix_engine_recommendation_sid", "engine_recommendation", ["sid", "as_of"])


def downgrade() -> None:
    op.drop_index("ix_engine_recommendation_sid", table_name="engine_recommendation")
    op.drop_table("engine_recommendation")
    op.drop_table("engine_run")
