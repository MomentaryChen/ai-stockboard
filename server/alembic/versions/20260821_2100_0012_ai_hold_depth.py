"""ai_hold_analysis: the same depth discriminator the technical lane already has

Revision ID: 0012_ai_hold_depth
Revises: 0011_ai_depth
Create Date: 2026-08-21 21:00:00.000000+00:00

The 存股 lane grows the second answer `0011_ai_depth` gave the technical one:
the same suitability question, asked of a company whose per-year earnings and
payout series, institutional flow, and valuation history are on the table
rather than only the checklist's aggregates. Both answers are about the same
company on the same trading day, so both belong in this table -- what has to
change is the key, or the second one overwrites the first.

Deliberately a copy of 0011 rather than something cleverer. The prefix on
`prompt_version` is what keeps this lane's wordings apart from the technical
lane's, and it can go on doing that; what it cannot do is separate two rows
inside *this* table, because a version string is a label and the uniqueness
here has to be enforced by the database.

Widening a unique index can only relax it: every existing row becomes `quick`,
and no pair of rows that was distinct before becomes equal. No backfill beyond
the server default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_ai_hold_depth"
down_revision: str | None = "0011_ai_depth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a default, for the reason 0011 gives: "no depth recorded" is
    # not a state any reader should have to handle, and every row that predates
    # this migration was a quick verdict by construction.
    op.add_column(
        "ai_hold_analysis",
        sa.Column(
            "depth", sa.String(length=8), nullable=False, server_default="quick"
        ),
    )
    op.create_check_constraint(
        "ck_ai_hold_analysis_depth", "ai_hold_analysis", "depth in ('quick', 'deep')"
    )

    op.drop_index("ux_ai_hold_analysis_subject", table_name="ai_hold_analysis")
    op.create_index(
        "ux_ai_hold_analysis_subject",
        "ai_hold_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale", "depth"],
        unique=True,
    )


def downgrade() -> None:
    # Dropping the column narrows the key again, so rows that differ only by
    # depth would collide. Delete the deep ones first -- they are a cache and
    # regenerate on demand, which is what makes this table safe to prune.
    op.execute(sa.text("delete from ai_hold_analysis where depth = 'deep'"))

    op.drop_index("ux_ai_hold_analysis_subject", table_name="ai_hold_analysis")
    op.create_index(
        "ux_ai_hold_analysis_subject",
        "ai_hold_analysis",
        ["sid", "as_of", "model", "prompt_version", "locale"],
        unique=True,
    )
    op.drop_constraint(
        "ck_ai_hold_analysis_depth", "ai_hold_analysis", type_="check"
    )
    op.drop_column("ai_hold_analysis", "depth")
