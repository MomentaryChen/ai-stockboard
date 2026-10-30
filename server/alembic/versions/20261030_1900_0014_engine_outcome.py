"""engine_outcome: how each earlier market engine call turned out

Revision ID: 0014_engine_outcome
Revises: 0013_engine_recommendation
Create Date: 2026-10-30 19:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_engine_outcome"
down_revision: str | None = "0013_engine_recommendation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_outcome",
        sa.Column("call_as_of", sa.Date(), primary_key=True),
        sa.Column("engine_version", sa.String(length=16), primary_key=True),
        sa.Column("sid", sa.String(length=16), primary_key=True),
        sa.Column("horizon", sa.String(length=8), primary_key=True),
        sa.Column("sector", sa.String(length=32), nullable=False),
        sa.Column("call", sa.String(length=12), nullable=False),
        sa.Column("hold_state", sa.String(length=12), nullable=False),
        sa.Column("filled", sa.Boolean(), nullable=False),
        sa.Column("stopped", sa.Boolean(), nullable=False),
        sa.Column("followed_return", sa.Float(), nullable=True),
        sa.Column("market_entry_return", sa.Float(), nullable=False),
        sa.Column("benchmark_return", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("engine_outcome")
