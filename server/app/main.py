"""ai-stockboard API.

A Taiwan stock market dashboard service. Market data (daily prices, realtime
quotes, the listed-company database) comes from the `twstock` library; this
service adds persistence, rate-limit handling, an HTTP API and a web UI on top,
and is where traditional and AI-assisted analysis are produced.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401  -- registers tables on Base.metadata
from app import schema_patches
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.routers import (
    analysis,
    auth,
    dividends,
    history,
    jobs,
    realtime,
    stocks,
    users,
    watchlist,
)
from app.schemas import HealthResponse
from app.services import auth as auth_service
from app.services import codes as codes_service
from app.services.jobs import scheduler as job_scheduler
from app.services.jobs import store as job_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        # create_all never ALTERs an existing table, so columns added after a
        # table shipped are applied separately. No-op on a fresh database.
        schema_patches.apply(engine)
        logger.info("Database tables ready")
    except Exception:
        # Let the app boot so /api/health can report *why* the DB is unreachable.
        logger.exception("Could not create tables -- is PostgreSQL running?")

    try:
        with SessionLocal() as db:
            auth_service.seed_admin(db)
    except Exception:
        # Same reasoning: a failed seed must not take the whole service down.
        logger.exception("Could not seed the ADMIN account")

    try:
        with SessionLocal() as db:
            job_store.backfill_legacy_sync_runs(db)
    except Exception:
        # Only the audit trail of the old single-job table; losing it must not
        # stop the service from booting.
        logger.exception("Could not backfill the legacy sync-run history")

    # Background jobs -- among them the one that seeds `stock_code` and
    # reconciles it with the exchanges' ISIN registry. Each runs on its own
    # thread: the first scrape takes the better part of a minute and must not
    # delay the port opening or the container healthcheck.
    job_scheduler.start()
    yield


app = FastAPI(
    title="ai-stockboard API",
    description=(
        "台股看板服務：歷史股價、除權息、即時報價、股票搜尋，"
        "以及傳統技術分析（AI 分析開發中）。行情資料來源為 twstock。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router)
app.include_router(jobs.router)
app.include_router(history.router)
app.include_router(dividends.router)
app.include_router(analysis.router)
app.include_router(analysis.batch_router)
app.include_router(realtime.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(watchlist.router)


# Deliberately unauthenticated: the compose healthcheck polls this, and a 401
# here would leave the server container unhealthy and the frontend never started.
@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "connected"
        status = "ok"
    except Exception as exc:
        database = f"unavailable: {exc.__class__.__name__}"
        status = "degraded"

    return HealthResponse(
        status=status,
        database=database,
        stock_codes_loaded=codes_service.code_count(),
        # Read off the cached listing, so this costs no extra query. None means
        # the listing on offer is still twstock's bundled snapshot.
        stock_codes_synced_at=codes_service.last_synced_at(),
    )


# Serve the production build when it exists (npm run build). Mounted last so the
# API routes above always win.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
