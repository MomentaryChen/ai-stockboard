"""Annual EPS and ROE, served database-first.

Today this module only reads and writes `fundamentals_annual`; nothing fills it
automatically. That is deliberate rather than unfinished. Choosing a provider
is a licensing and reliability decision (FinMind's free tier, MOPS scraping,
and a hand-maintained CSV all have different answers), and the 存股 checklist
above it was built to report `unknown` for the dimensions it cannot compute --
so shipping the store and the seam ahead of the ingest costs nothing and lets
every caller be written against the shape the ingest will fill.

`upsert()` is the seam. A provider client is a function that produces `Row`s;
it does not need to know about the table, the fetch log or the conflict
handling, all of which are here. `record_fetch()` mirrors the dividend
ingest's own fetch stamp, so a future job can say what it pulled and when in
the vocabulary the other ingests already use.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import FundamentalsAnnual, FundamentalsFetchLog

logger = logging.getLogger(__name__)

#: Everything the checklist reads. Wider than what any one provider returns, so
#: a client fills what it has and leaves the rest None -- the store keeps
#: "unknown" and "zero" distinct all the way to the card.
_UPDATABLE = ("eps", "roe", "net_income", "equity", "source")


@dataclass(frozen=True, slots=True)
class Row:
    """One company-year, as a provider client produces it."""

    sid: str
    year: int
    eps: float | None = None
    roe: float | None = None
    net_income: float | None = None
    equity: float | None = None
    source: str = "manual"


def read(db: Session, sid: str, years: int) -> list[FundamentalsAnnual]:
    """Stored company-years for one sid, oldest first.

    Never fetches. Unlike history and dividends there is no lazy upstream to
    fall back to, so an empty list means the ingest has not covered this name --
    which the checklist reports rather than treats as a failure.
    """
    today = datetime.date.today()
    return list(
        db.execute(
            select(FundamentalsAnnual)
            .where(
                FundamentalsAnnual.sid == sid,
                FundamentalsAnnual.year > today.year - years - 1,
            )
            .order_by(FundamentalsAnnual.year)
        ).scalars()
    )


def upsert(db: Session, rows: list[Row]) -> int:
    """Write company-years, replacing what a previous pull left.

    Replacing rather than merging: a provider correcting a restated figure has
    to be able to overwrite it, and a partial row from a thinner source would
    otherwise be indistinguishable from a correction that blanked a field.
    """
    if not rows:
        return 0

    payload = [
        {
            "sid": row.sid,
            "year": row.year,
            "eps": row.eps,
            "roe": row.roe,
            "net_income": row.net_income,
            "equity": row.equity,
            "source": row.source[:16],
        }
        for row in rows
    ]
    stmt = pg_insert(FundamentalsAnnual).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalsAnnual.sid, FundamentalsAnnual.year],
        set_={c: stmt.excluded[c] for c in _UPDATABLE},
    )
    db.execute(stmt)
    return len(payload)


def record_fetch(db: Session, source: str, bucket: str, count: int) -> None:
    """Stamp one provider pull, the same way the dividend ingest does."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = pg_insert(FundamentalsFetchLog).values(
        source=source, bucket=bucket, row_count=count, fetched_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalsFetchLog.source, FundamentalsFetchLog.bucket],
        set_={
            "row_count": stmt.excluded.row_count,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    db.execute(stmt)
