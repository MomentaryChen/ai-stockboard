"""valuation_day: the exchange's own PE, PBR and yield, one row per session

Revision ID: 0010_valuation_day
Revises: 0009_chen_hold
Create Date: 2026-08-21 20:00:00.000000+00:00

TWSE publishes 個股日本益比、殖利率及股價淨值比 (BWIBBU) and TPEx publishes the
same three figures, both as all-market daily reports -- one request covers
every listed name, exactly like the T86 / MI_MARGN reports `chip_day` already
stores. So this table is shaped like `chip_day` rather than like
`fundamentals_annual`: the grain is a trading session, not a fiscal year.

Storing it rather than reading it live buys two things. The 存股 checklist gets
the exchange's own PE instead of one derived from a year-old annual EPS -- and
because the rows accumulate, "is this cheap against its own five-year band"
becomes answerable later without a second source.

Every figure is nullable. The exchange leaves PE blank for a company with no
positive trailing earnings, and blank is a different statement from zero: it
means the ratio is undefined, which the checklist reports as `unknown` rather
than as a company that failed a valuation test.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_valuation_day"
down_revision: str | None = "0009_chen_hold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuation_day",
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # Numeric rather than float: these are published to two decimals and a
        # PE of 10.00 must not come back as 9.999999 in a threshold comparison.
        sa.Column("pe_ratio", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("pb_ratio", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("dividend_yield", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="twse"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sid", "date"),
    )
    # The hold snapshot reads the newest row for one sid; the future five-year
    # band will read a range of them. Same index `chip_day` carries, for the
    # same two access patterns.
    op.create_index("ix_valuation_day_sid_date", "valuation_day", ["sid", "date"])


def downgrade() -> None:
    op.drop_index("ix_valuation_day_sid_date", table_name="valuation_day")
    op.drop_table("valuation_day")
