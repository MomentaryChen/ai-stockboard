"""ai_analysis: a depth discriminator so quick and deep verdicts coexist

Revision ID: 0011_ai_depth
Revises: 0010_valuation_day
Create Date: 2026-08-21 20:30:00.000000+00:00

The technical lane grows a second, more expensive answer: the same enter/exit/
hold question, asked with institutional flow and annual fundamentals alongside
the price series. Both answers are about the same stock on the same trading
day, so both belong in this table -- what has to change is the key, or the
second one would overwrite the first.

`depth` rather than a `deep-` prefix on `prompt_version`. The hold lane leans
on such a prefix, but it is decoration there: that lane already has its own
table, so the prefix cannot be the thing keeping two verdicts apart. Here it
would be exactly that, and a version string is the wrong place to put a
discriminator the database has to enforce -- one rename and two different
engines start sharing a row.

Widening a unique index can only relax it, never break it: every existing row
becomes `quick`, and no pair of rows that was distinct before becomes equal.
So this needs no backfill beyond the server default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_ai_depth"
down_revision: str | None = "0010_valuation_day"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a default rather than nullable: "no depth recorded" is not
    # a state any reader should have to handle, and every row that predates
    # this migration was a quick verdict by construction.
    op.add_column(
        "ai_analysis",
        sa.Column(
            "depth", sa.String(length=8), nullable=False, server_default="quick"
        ),
    )
    op.create_check_constraint(
        "ck_ai_analysis_depth", "ai_analysis", "depth in ('quick', 'deep')"
    )

    op.drop_index("ux_ai_analysis_subject", table_name="ai_analysis")
    op.create_index(
        "ux_ai_analysis_subject",
        "ai_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale", "depth"],
        unique=True,
    )


def downgrade() -> None:
    # Dropping the column narrows the key again, so rows that differ only by
    # depth would collide. Delete the deep ones first -- they are a cache and
    # regenerate on demand, which is the whole reason this table is safe to
    # prune.
    op.execute(sa.text("delete from ai_analysis where depth = 'deep'"))

    op.drop_index("ux_ai_analysis_subject", table_name="ai_analysis")
    op.create_index(
        "ux_ai_analysis_subject",
        "ai_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale"],
        unique=True,
    )
    op.drop_constraint("ck_ai_analysis_depth", "ai_analysis", type_="check")
    op.drop_column("ai_analysis", "depth")
