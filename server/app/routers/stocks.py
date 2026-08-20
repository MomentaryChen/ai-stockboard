from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import AppUser
from app.schemas import CodeSyncResponse, SearchResponse, StockInfo
from app.services import code_sync
from app.services import codes as codes_service
from app.services.jobs import registry, runner

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


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


@router.post("/sync", response_model=CodeSyncResponse, deprecated=True)
def sync_stock_codes(
    force: bool = Query(
        True, description="忽略同步間隔，立即向交易所 ISIN 名冊重抓"
    ),
    admin: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> CodeSyncResponse:
    """Reconcile `stock_code` with the exchanges' registry, and wait for it.

    Superseded by `POST /api/jobs/stock_code_sync/run`, which the admin UI uses:
    that one returns as soon as the run has started, which is what a page
    listing several jobs needs. This route is kept because it is the one
    documented for curl, and a script wants the outcome in the response rather
    than a second call to fetch it.

    Runs through the same runner as everything else, so the concurrency lock,
    the cooldown and the audit trail all apply -- it blocks for as long as the
    scrape takes (~40 s).
    """
    job = registry.get(registry.STOCK_CODE_SYNC)
    try:
        runner.check_manual_allowed(job)
        record = runner.run_job(
            job, trigger=runner.TRIGGER_MANUAL, actor=admin.username, force=force
        )
    except runner.JobBusyError as exc:
        raise HTTPException(status_code=409, detail="This job is already running") from exc
    except runner.CooldownError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    stats = record.stats or {}
    return CodeSyncResponse(
        # The job tables say "success"; this response has always said "synced".
        status="synced" if record.status == "success" else record.status,
        synced_at=code_sync.last_synced_at(db),
        active=stats.get("active", 0),
        inserted=stats.get("inserted", 0),
        updated=stats.get("updated", 0),
        delisted=stats.get("delisted", 0),
        pruned=stats.get("pruned", 0),
        message=record.message,
    )


@router.get("/{sid}", response_model=StockInfo)
def get_stock(sid: str) -> StockInfo:
    info = codes_service.get_stock(sid)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Stock ID '{sid}' not found")
    return info
