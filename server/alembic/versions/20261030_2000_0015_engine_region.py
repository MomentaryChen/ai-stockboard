"""engine tables gain a region, so US and Taiwan runs can share a date

Revision ID: 0015_engine_region
Revises: 0014_engine_outcome
Create Date: 2026-10-30 20:00:00.000000+00:00

Every existing row came from the Taiwan engine, so the server default backfills
them correctly. It stays in place because `region` joins each primary key, and
that is the only thing a new row needs from it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_engine_region"
down_revision: str | None = "0014_engine_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYS = {
    "engine_run": ["as_of", "engine_version"],
    "engine_recommendation": ["as_of", "engine_version", "sid"],
    "engine_outcome": ["call_as_of", "engine_version", "sid", "horizon"],
}


def upgrade() -> None:
    for table, key in _KEYS.items():
        op.add_column(table, sa.Column("region", sa.String(length=4), nullable=False, server_default="tw"))
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, ["region", *key])
    op.drop_index("ix_engine_recommendation_sid", table_name="engine_recommendation")
    op.create_index("ix_engine_recommendation_sid", "engine_recommendation", ["region", "sid", "as_of"])


def downgrade() -> None:
    # The old keys cannot hold two regions on one date, and these rows have
    # nowhere else to go.
    for table in _KEYS:
        op.execute(sa.text(f"delete from {table} where region <> 'tw'"))
    op.drop_index("ix_engine_recommendation_sid", table_name="engine_recommendation")
    op.create_index("ix_engine_recommendation_sid", "engine_recommendation", ["sid", "as_of"])
    for table, key in _KEYS.items():
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, key)
        op.drop_column(table, "region")
