"""Whether the 存股 thresholds were followed by better outcomes.

ADMIN only, and on its own router so a route added later inherits the guard.
`publish` already blanks returns while the price backfill is unfinished. The
guard is what keeps the counts -- which name the universe -- off the public
API as well. This is a statement about the checklist, not a market-data card.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.schemas import ScoreStudyResponse
from app.services.analysis import score_study

router = APIRouter(
    prefix="/api/admin",
    tags=["score-study"],
    dependencies=[Depends(require_admin)],
)


@router.get("/score-study", response_model=ScoreStudyResponse)
def get_score_study(
    horizon_years: int = Query(
        score_study.DEFAULT_HORIZON_YEARS,
        ge=3,
        le=8,
        description=(
            "Years held after the decision date. Three is the floor because a "
            "two-year window minus the holiday slack at each end falls under "
            "the hold replay's two-year minimum and would be reported as too "
            "thin rather than as a two-year result."
        ),
    ),
    as_of: datetime.date | None = Query(
        None,
        description=(
            "Decision date, shared by every name. Omit it and the study uses "
            "the last stored index session minus the horizon, so the outcome "
            "window ends on data we actually have."
        ),
    ),
    db: Session = Depends(get_db),
) -> ScoreStudyResponse:
    return score_study.load(db, horizon_years=horizon_years, as_of=as_of)
