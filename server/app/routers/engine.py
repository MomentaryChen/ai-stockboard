"""The sector entry-price pages.

Signed-in only, and guarded on the router so a route added later inherits it.
These responses name a price to buy at, which is a different thing from the
market data the public pages show: opening them up is a licensing question, not
a product setting.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas import (
    EngineOverviewResponse,
    EngineRecommendationItem,
    EngineSectorSummary,
    EngineStockResponse,
    EngineTrackRecordResponse,
)
from app.services import engine_import, engine_track

router = APIRouter(
    prefix="/api/engine",
    tags=["engine"],
    dependencies=[Depends(get_current_user)],
)

#: A market closure lasts about a week at most, so anything older than this
#: means the batch has stopped, and the page should say so.
STALE_AFTER_DAYS = 7


def _is_stale(as_of: datetime.date) -> bool:
    return (datetime.date.today() - as_of).days > STALE_AFTER_DAYS


def _buy_now(plan: dict | None) -> bool:
    return bool(plan and plan.get("entry") and plan["entry"]["k_atr"] == 0)


@router.get("/overview", response_model=EngineOverviewResponse)
def overview(db: Session = Depends(get_db)) -> EngineOverviewResponse:
    run = engine_import.latest_run(db)
    if run is None:
        return EngineOverviewResponse()
    sectors: dict[str, EngineSectorSummary] = {}
    for row in engine_import.run_rows(db, run):
        summary = sectors.setdefault(row.sector, EngineSectorSummary(sector=row.sector))
        summary.stocks += 1
        summary.short_now += _buy_now(row.payload.get("short"))
        summary.long_now += _buy_now(row.payload.get("long"))
        summary.weak += row.hold_state == "weak"
    return EngineOverviewResponse(
        as_of=run.as_of,
        engine_version=run.engine_version,
        generated_at=run.generated_at,
        imported_at=run.imported_at,
        stale=_is_stale(run.as_of),
        sectors=list(sectors.values()),
        hold_backtest=run.hold_backtest,
    )


@router.get("/recommendations", response_model=list[EngineRecommendationItem])
def recommendations(
    sector: str = Query(..., max_length=32),
    db: Session = Depends(get_db),
) -> list[EngineRecommendationItem]:
    run = engine_import.latest_run(db)
    if run is None:
        return []
    return [EngineRecommendationItem(**row.payload) for row in engine_import.run_rows(db, run, sector=sector)]


@router.get("/track-record", response_model=EngineTrackRecordResponse)
def track_record(db: Session = Depends(get_db)) -> EngineTrackRecordResponse:
    return EngineTrackRecordResponse(**engine_track.load(db))


@router.get("/stocks/{sid}", response_model=EngineStockResponse)
def stock(sid: str, db: Session = Depends(get_db)) -> EngineStockResponse:
    """A 200 with `item: null` for an uncovered stock.

    Only the top names in each sector are scored, so "not covered" is the normal
    answer for most of the market rather than an error.
    """
    run = engine_import.latest_run(db)
    if run is None:
        return EngineStockResponse()
    row = engine_import.stock_row(db, run, sid)
    return EngineStockResponse(
        as_of=run.as_of,
        stale=_is_stale(run.as_of),
        item=EngineRecommendationItem(**row.payload) if row else None,
    )
