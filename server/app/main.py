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

from app import logging_config, migrations
from app import models  # noqa: F401  -- maps every table before the first query
from app.config import get_settings
from app.db import SessionLocal, engine
from app.middleware import RequestContextMiddleware
from app.routers import (
    ai_settings,
    analysis,
    auth,
    chips,
    dividends,
    history,
    jobs,
    market,
    realtime,
    stocks,
    users,
    watchlist,
)
from app.schemas import BackupHealth, HealthResponse, JobHealth
from app.services import auth as auth_service
from app.services import codes as codes_service
from app.services import health as health_service
from app.services.jobs import scheduler as job_scheduler

settings = get_settings()

# Before anything else logs: uvicorn has already configured the root logger
# by the time it imports this module, and this is what takes it back --
# request ids on every line, and one access line per request instead of
# uvicorn's. See app/logging_config.py.
logging_config.configure(level=settings.log_level, fmt=settings.log_format)
logger = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        migrations.upgrade_to_head(engine)
    except Exception:
        # Let the app boot so /api/health can report *why* the DB is unreachable.
        # A schema that is genuinely behind will surface as failing queries
        # rather than a silent wrong answer, which is the trade this has always
        # made in exchange for a diagnosable health endpoint.
        logger.exception("Could not migrate the database -- is PostgreSQL running?")

    try:
        with SessionLocal() as db:
            auth_service.seed_admin(db)
    except Exception:
        # Same reasoning: a failed seed must not take the whole service down.
        logger.exception("Could not seed the ADMIN account")

    # Background jobs -- among them the one that seeds `stock_code` and
    # reconciles it with the exchanges' ISIN registry. Each runs on its own
    # thread: the first scrape takes the better part of a minute and must not
    # delay the port opening or the container healthcheck.
    job_scheduler.start()
    yield


app = FastAPI(
    title="ai-stockboard API",
    description=(
        "台股看板服務：歷史股價、除權息、籌碼、即時報價、股票搜尋，"
        "以及規則式技術分析、訊號回測與 AI 進出場評估。行情資料來源為 twstock。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Added last, so it wraps CORS and everything below it: a request rejected
# at the CORS layer should still get an id and still be logged.
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router)
app.include_router(jobs.router)
app.include_router(ai_settings.router)
app.include_router(history.router)
app.include_router(dividends.router)
app.include_router(chips.router)
app.include_router(analysis.router)
app.include_router(analysis.batch_router)
app.include_router(market.router)
app.include_router(realtime.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(watchlist.router)


# Deliberately unauthenticated: the compose healthcheck polls this, and a 401
# here would leave the server container unhealthy and the frontend never started.
#
# It answers 200 whatever it finds -- `status` carries the verdict. A non-2xx
# here would fail the container healthcheck and take the frontend down with it,
# which is the wrong response to "last night's backup did not run".
@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "connected"
        database_ok = True
    except Exception as exc:
        database = f"unavailable: {exc.__class__.__name__}"
        database_ok = False

    # The job table is only readable when the database is; skip rather than
    # raise, so an unreachable database reports itself instead of a 500.
    failing_jobs: list[str] = []
    last_failure_at = None
    if database_ok:
        try:
            with SessionLocal() as db:
                failing_jobs, last_failure_at = health_service.job_health(db)
        except Exception:
            logger.exception("could not read job health")

    backup_status, backup_taken_at, backup_age_hours = health_service.backup_health()

    problems = health_service.alerts(
        database_ok=database_ok,
        failing_jobs=failing_jobs,
        backup_status=backup_status,
        backup_age_hours=backup_age_hours,
    )

    return HealthResponse(
        status="ok" if not problems else "degraded",
        database=database,
        stock_codes_loaded=codes_service.code_count(),
        # Read off the cached listing, so this costs no extra query. None means
        # the listing on offer is still twstock's bundled snapshot.
        stock_codes_synced_at=codes_service.last_synced_at(),
        jobs=JobHealth(failing=failing_jobs, last_failure_at=last_failure_at),
        backup=BackupHealth(
            status=backup_status,
            taken_at=backup_taken_at,
            age_hours=backup_age_hours,
        ),
        alerts=problems,
    )


# Serve the production build when it exists (npm run build). Mounted last so the
# API routes above always win.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
