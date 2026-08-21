"""chip flow tables

Revision ID: 0006_chip_flow
Revises: 0005_ai_analysis
Create Date: 2026-08-21 16:00:00.000000+00:00

Two new tables, nothing else touched.

`chip_day` is one stock's 法人 nets and 融資融券 balances for one session.
`chip_fetch_log` stamps the all-market daily reports those rows came from
(T86 / MI_MARGN on TWSE, 3insti / margin on TPEX), keyed by date rather than
sid so the second stock to ask for the same session is a cache hit.

Both are source data, not derived: dropping them on downgrade loses the
cached reports and they are re-fetched from the exchange on the next page
view, the same way `daily_price` is.

Numbered 0006 because 0005 is the AI analysis cache that landed on develop
first; a branched 0005 would fail the single-chain revision test.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_chip_flow"
down_revision: str | None = "0005_ai_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chip_day",
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("foreign_net", sa.BigInteger(), nullable=True),
        sa.Column("trust_net", sa.BigInteger(), nullable=True),
        sa.Column("dealer_net", sa.BigInteger(), nullable=True),
        sa.Column("total_net", sa.BigInteger(), nullable=True),
        sa.Column("margin_balance", sa.Integer(), nullable=True),
        sa.Column("margin_change", sa.Integer(), nullable=True),
        sa.Column("short_balance", sa.Integer(), nullable=True),
        sa.Column("short_change", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sid", "date"),
    )
    op.create_index("ix_chip_day_sid_date", "chip_day", ["sid", "date"], unique=False)

    op.create_table(
        "chip_fetch_log",
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("report", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "report", "date"),
    )


def downgrade() -> None:
    op.drop_table("chip_fetch_log")
    op.drop_index("ix_chip_day_sid_date", table_name="chip_day")
    op.drop_table("chip_day")
