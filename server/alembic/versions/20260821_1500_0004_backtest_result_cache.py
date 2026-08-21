"""backtest result cache

Revision ID: 0004_backtest_result_cache
Revises: 0003_account_review_and_lockout
Create Date: 2026-08-21 15:00:00.000000+00:00

One new table, `backtest_result`, and nothing else touched.

Every row in it is derived: `services/analysis/backtest.py` can rebuild any of
them from `daily_price`, and the endpoint does exactly that whenever a row is
missing or older than the bars behind it. That makes `downgrade` a plain drop
with nothing to preserve, and it makes an empty table after this migration the
correct state rather than a gap to backfill -- the nightly job and the first
page view will fill it between them.

Keyed on (sid, rule_set): the corrected and the upstream rules disagree often
enough that a win rate without the rule set attached is not an answer. The
trailing window is a column rather than a third key column deliberately -- see
the model's docstring for why only one window is offered.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_backtest_result_cache"
down_revision: str | None = "0003_account_review_and_lockout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_result",
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("rule_set", sa.String(length=16), nullable=False),
        sa.Column("window_months", sa.SmallInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("bars", sa.Integer(), nullable=False),
        sa.Column("judged_days", sa.Integer(), nullable=False),
        sa.Column("buy_signals", sa.Integer(), nullable=False),
        sa.Column("sell_signals", sa.Integer(), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("trade_wins", sa.Integer(), nullable=False),
        sa.Column("strategy_return", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("buy_hold_return", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "buy_hold_max_drawdown", sa.Numeric(precision=12, scale=6), nullable=True
        ),
        sa.Column("exposure", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("computed_through", sa.Date(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sid", "rule_set"),
    )
    op.create_index(
        "ix_backtest_result_rule_set", "backtest_result", ["rule_set"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_result_rule_set", table_name="backtest_result")
    op.drop_table("backtest_result")
