from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_admin
from app.models import AppUser
from app.schemas import (
    CodeSyncResponse,
    SearchResponse,
    StockInfo,
    SyncRun,
    SyncRunsResponse,
)
from app.services import code_sync
from app.services import codes as codes_service

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

settings = get_settings()


# Declared before /{sid} so "search" is not swallowed by the path parameter.
@router.get("/search", response_model=SearchResponse)
def search_stocks(
    q: str = Query(..., min_length=1, description="股票代碼或名稱關鍵字"),
    limit: int = Query(30, ge=1, le=100),
    include_warrants: bool = Query(
        False, description="一併搜尋認購(售)權證（約 4.2 萬檔，預設排除）"
    ),
) -> SearchResponse:
    total, results = codes_service.search(q, limit, include_warrants=include_warrants)
    return SearchResponse(query=q, total=total, results=results)


@router.post("/sync", response_model=CodeSyncResponse)
def sync_stock_codes(
    force: bool = Query(
        False, description="忽略同步間隔，立即向交易所 ISIN 名冊重抓"
    ),
    _admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> CodeSyncResponse:
    """Reconcile `stock_code` with the exchanges' registry, now.

    The scheduler in app/services/code_sync.py already does this daily; this is
    the manual lever for the day a company lists and someone needs it visible
    before tomorrow. It blocks for as long as the scrape takes (~30 s).
    """
    report = code_sync.run(db, force=force)
    return CodeSyncResponse(
        status=report.status,
        synced_at=report.synced_at,
        active=report.active,
        inserted=report.inserted,
        updated=report.updated,
        delisted=report.delisted,
        pruned=report.pruned,
        message=report.message,
    )


@router.get("/sync/runs", response_model=SyncRunsResponse)
def list_sync_runs(
    limit: int = Query(50, ge=1, le=200, description="最多回傳幾筆執行紀錄"),
    _admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SyncRunsResponse:
    """The batch job's audit trail, newest first.

    Reports `enabled` and `interval_hours` alongside the rows because a silent
    log is ambiguous on its own: a job that is switched off looks exactly like
    one that has crashed.
    """
    total, rows = code_sync.list_runs(db, limit=limit)
    return SyncRunsResponse(
        enabled=settings.stock_code_sync_enabled,
        interval_hours=settings.stock_code_sync_interval_hours,
        last_success_at=code_sync.last_success_at(db),
        synced_at=code_sync.last_synced_at(db),
        active=codes_service.code_count(),
        total=total,
        runs=[
            SyncRun(
                id=row.id,
                started_at=row.started_at,
                finished_at=row.finished_at,
                duration_seconds=round(
                    (row.finished_at - row.started_at).total_seconds(), 1
                ),
                status=row.status,
                trigger=row.trigger,
                sources=[s for s in row.sources.split(",") if s],
                active=row.active,
                inserted=row.inserted,
                updated=row.updated,
                delisted=row.delisted,
                pruned=row.pruned,
                message=row.message,
            )
            for row in rows
        ],
    )


@router.get("/{sid}", response_model=StockInfo)
def get_stock(sid: str) -> StockInfo:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")
    return info
