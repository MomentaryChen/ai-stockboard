"""chen hold analysis: annual fundamentals and the hold verdict cache

Revision ID: 0008_chen_hold
Revises: 0007_watchlist_groups
Create Date: 2026-08-21 19:00:00.000000+00:00

Three tables for a second analysis lane that asks a different question from the
technical one: not "what should I do with this position today" but "is this a
company worth accumulating and holding for the dividend".

`fundamentals_annual` and `fundamentals_fetch_log` are created empty. No
provider is wired yet -- that is a later milestone -- and creating them now is
deliberate: the hold feature set reads them from day one and reports the
dimensions it cannot score as `unknown` rather than pretending. A schema that
arrives with the ingest would make "we have no EPS" indistinguishable from "the
table does not exist yet" for every caller written in between.

`ai_hold_analysis` mirrors `ai_analysis` down to the unique key, and is a
separate table rather than a discriminator column on that one because the two
verdicts have no columns in common: one is action + size, the other is
suitability over a multi-year horizon. Sharing a table would mean every column
being nullable and every reader knowing which half applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_chen_hold"
down_revision: str | None = "0007_watchlist_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamentals_annual",
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("eps", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("roe", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("net_income", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("equity", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sid", "year"),
    )

    op.create_table(
        "fundamentals_fetch_log",
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("bucket", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "bucket"),
    )

    op.create_table(
        "ai_hold_analysis",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sid", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("suitability", sa.String(length=8), nullable=False),
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
        # Null when the model declined to say. Kept nullable rather than
        # defaulted so "did not answer" and "disagrees" stay distinguishable.
        sa.Column("agrees_with_rules", sa.Boolean(), nullable=True),
        # What the deterministic checklist said for the same snapshot, stored
        # alongside so the two can be compared later without re-deriving the
        # rules as they were on the day.
        sa.Column("rule_score", sa.Integer(), nullable=True),
        sa.Column("rule_suitability", sa.String(length=8), nullable=True),
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
        # SET NULL, as on ai_analysis: closing an account must not erase what
        # it spent.
        sa.ForeignKeyConstraint(["requested_by"], ["app_user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "suitability in ('strong', 'ok', 'weak', 'avoid')",
            name="ck_ai_hold_analysis_suitability",
        ),
        sa.CheckConstraint(
            "confidence in ('high', 'medium', 'low')",
            name="ck_ai_hold_analysis_confidence",
        ),
        sa.CheckConstraint(
            "rule_suitability is null or rule_suitability in "
            "('strong', 'ok', 'weak', 'avoid')",
            name="ck_ai_hold_analysis_rule_suitability",
        ),
    )
    op.create_index(
        "ux_ai_hold_analysis_subject",
        "ai_hold_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale"],
        unique=True,
    )
    # The daily quota counts one account's paid rows across both AI tables.
    op.create_index(
        "ix_ai_hold_analysis_requested_by_created",
        "ai_hold_analysis",
        ["requested_by", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_hold_analysis_requested_by_created", table_name="ai_hold_analysis"
    )
    op.drop_index("ux_ai_hold_analysis_subject", table_name="ai_hold_analysis")
    op.drop_table("ai_hold_analysis")
    op.drop_table("fundamentals_fetch_log")
    op.drop_table("fundamentals_annual")
