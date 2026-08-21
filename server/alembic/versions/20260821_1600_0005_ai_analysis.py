"""ai_analysis: the shared cache of generated position calls

Revision ID: 0005_ai_analysis
Revises: 0004_backtest_result_cache
Create Date: 2026-08-21 11:30:00.000000+00:00

One row per (sid, trading day, model, prompt version, locale). The unique index
is the feature's cost control, not a data-hygiene nicety: without it every press
of the button is a paid request, and a watchlist board would bill once per card
per visitor. With it, the second reader of the same session's verdict is free.

`model` and `prompt_version` belong in the key because verdicts are only
comparable when they were produced the same way -- an evaluation that mixed two
prompts would report the difference between them as a change in the market.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_ai_analysis"
down_revision: str | None = "0004_backtest_result_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("size", sa.String(length=8), nullable=True),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("headline", sa.String(length=500), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "risks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # SET NULL: closing an account must not erase what it spent.
        sa.ForeignKeyConstraint(
            ["requested_by"], ["app_user.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "action in ('enter', 'exit', 'hold')", name="ck_ai_analysis_action"
        ),
        sa.CheckConstraint(
            "size is null or size in ('large', 'medium', 'small')",
            name="ck_ai_analysis_size",
        ),
        # Sizing is meaningless on hold and mandatory on enter/exit, so the
        # database refuses the two combinations the card could not render.
        sa.CheckConstraint(
            "(action = 'hold') = (size is null)",
            name="ck_ai_analysis_size_matches_action",
        ),
    )
    op.create_index(
        "ux_ai_analysis_subject",
        "ai_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale"],
        unique=True,
    )
    op.create_index(
        "ix_ai_analysis_requested_by_created",
        "ai_analysis",
        ["requested_by", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_analysis_requested_by_created", table_name="ai_analysis")
    op.drop_index("ux_ai_analysis_subject", table_name="ai_analysis")
    op.drop_table("ai_analysis")
