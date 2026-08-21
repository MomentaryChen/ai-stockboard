"""Chip flow: institutional net buying and margin balances.

A sibling of `/dividends`, not of `/analysis/traditional`. The card on the
stock page is information; it does not feed 四大買賣點. Metered like
`/history`: `force` is ADMIN, and an anonymous caller may not ask for more
days than the public chart shows.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import deps
from app.db import get_db
from app.models import AppUser
from app.schemas import ChipDayOut, ChipFlow, ChipResponse
from app.services import chip as chip_service
from app.services import codes as codes_service
from app.services import market_index

router = APIRouter(prefix="/api/stocks", tags=["chips"])


def _flow(flow: chip_service.ChipFlow) -> ChipFlow:
    return ChipFlow(
        streak=flow.streak,  # type: ignore[arg-type]
        streak_days=flow.streak_days,
        net_5d=flow.net_5d,
    )


def _empty_response(
    sid: str, name: str, source: str, days: int, coverage: str
) -> ChipResponse:
    empty = _flow(chip_service.empty_flow())
    return ChipResponse(
        sid=sid,
        name=name,
        source=source,  # type: ignore[arg-type]
        coverage=coverage,  # type: ignore[arg-type]
        days=days,
        as_of=None,
        count=0,
        fetched_dates=[],
        cached_dates=[],
        foreign=empty,
        trust=empty,
        dealer=empty,
        total=empty,
        margin_balance=None,
        margin_change=None,
        short_balance=None,
        short_change=None,
        rows=[],
    )


@router.get("/{sid}/chips", response_model=ChipResponse)
def get_chips(
    sid: str,
    days: int = Query(
        chip_service.DEFAULT_DAYS,
        ge=1,
        le=chip_service.MAX_DAYS,
        description="How many recent trading days to show (from daily_price)",
    ),
    force: bool = Depends(deps.force_refresh),
    user: AppUser | None = Depends(deps.get_optional_user),
    db: Session = Depends(get_db),
) -> ChipResponse:
    deps.limit_anonymous_window(
        user, days, chip_service.ANONYMOUS_MAX_DAYS, "days"
    )

    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")

    if market_index.is_index(sid):
        return _empty_response(sid, info.name, info.data_source, days, "none")

    rows, fetched, cached = chip_service.get_chips(db, sid, days, force=force)
    latest = rows[0] if rows else None
    return ChipResponse(
        sid=sid,
        name=info.name,
        source=info.data_source,
        coverage="daily",
        days=days,
        as_of=latest.date if latest else None,
        count=len(rows),
        fetched_dates=fetched,
        cached_dates=cached,
        foreign=_flow(chip_service.flow_for(rows, "foreign_net")),
        trust=_flow(chip_service.flow_for(rows, "trust_net")),
        dealer=_flow(chip_service.flow_for(rows, "dealer_net")),
        total=_flow(chip_service.flow_for(rows, "total_net")),
        margin_balance=latest.margin_balance if latest else None,
        margin_change=latest.margin_change if latest else None,
        short_balance=latest.short_balance if latest else None,
        short_change=latest.short_change if latest else None,
        rows=[
            ChipDayOut(
                date=row.date,
                foreign_net=row.foreign_net,
                trust_net=row.trust_net,
                dealer_net=row.dealer_net,
                total_net=row.total_net,
                margin_balance=row.margin_balance,
                margin_change=row.margin_change,
                short_balance=row.short_balance,
                short_change=row.short_change,
            )
            for row in rows
        ],
    )
