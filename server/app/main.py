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
from app.config import get_settings
from app.db import Base, engine
from app.routers import analysis, history, realtime, stocks
from app.schemas import HealthResponse
from app.services import codes as codes_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ready")
    except Exception:
        # Let the app boot so /api/health can report *why* the DB is unreachable.
        logger.exception("Could not create tables -- is PostgreSQL running?")
    yield


app = FastAPI(
    title="ai-stockboard API",
    description=(
        "台股看板服務：歷史股價、即時報價、股票搜尋，"
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
app.include_router(history.router)
app.include_router(analysis.router)
app.include_router(realtime.router)


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
    )


# Serve the production build when it exists (npm run build). Mounted last so the
# API routes above always win.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
