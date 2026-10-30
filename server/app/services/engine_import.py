"""Loads the market engine's daily file into the database.

The engine runs in its own container and hands over one JSON file, so this is a
trust boundary even though both sides live in one repository: the two can be
deployed at different versions. Everything is validated before anything is
written, and a bad file leaves yesterday's rows on the page.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import EngineOutcome, EngineRecommendation, EngineRun

logger = logging.getLogger(__name__)

#: 1 is accepted so the first import after an upgrade can still read the file the
#: previous engine version left behind.
SUPPORTED_SCHEMAS = {1, 2}


class EngineEntry(BaseModel):
    k_atr: float
    price: float
    ev: float
    ev_lo: float
    win_rate: float
    worst_5pct: float
    fill_rate: float
    max_weight: float


class EnginePlan(BaseModel):
    ev: float | None
    worst_5pct: float | None
    trust: Literal["low", "medium"]
    entry: EngineEntry | None


class EngineRow(BaseModel):
    sid: str
    name: str
    market: str
    sector: str
    sector_rank: int
    market_cap: float
    status: Literal["ok", "insufficient_history", "data_mismatch", "failed"]
    status_detail: str | None
    close: float | None
    as_of: datetime.date | None
    short: EnginePlan | None
    long: EnginePlan | None
    hold_state: Literal["strong", "unclear", "weak", "unknown"]
    risk_line: float | None


class EngineValidationRow(BaseModel):
    state: Literal["strong", "unclear", "weak", "unknown"]
    samples: int
    excess_20d: float
    excess_60d: float
    excess_60d_lo: float | None
    excess_60d_hi: float | None
    up_rate_60d: float


class EngineOutcomeRow(BaseModel):
    call_as_of: datetime.date
    engine_version: str
    sid: str
    sector: str
    horizon: Literal["short", "long"]
    call: Literal["buy_now", "buy_dip", "no_buy"]
    hold_state: Literal["strong", "unclear", "weak", "unknown"]
    filled: bool
    stopped: bool
    followed_return: float | None
    market_entry_return: float
    benchmark_return: float | None


class EngineFile(BaseModel):
    schema_version: int
    engine_version: str
    generated_at: datetime.datetime
    as_of: datetime.date
    recommendations: list[EngineRow]
    hold_backtest: list[EngineValidationRow]
    outcomes: list[EngineOutcomeRow] = []


@dataclass(frozen=True)
class ImportReport:
    status: str  # imported | unchanged | missing | not_configured
    message: str | None = None
    rows: int = 0
    scored: int = 0
    graded: int = 0


class EngineFileError(ValueError):
    """The file exists but cannot be trusted. Reported, never partly applied."""


def parse(raw: bytes) -> EngineFile:
    try:
        parsed = EngineFile.model_validate_json(raw)
    except ValidationError as error:
        raise EngineFileError(f"Engine file failed validation: {error.error_count()} problem(s)") from error
    if parsed.schema_version not in SUPPORTED_SCHEMAS:
        raise EngineFileError(f"Engine file schema {parsed.schema_version} is not supported")
    if not parsed.recommendations:
        raise EngineFileError("Engine file has no recommendations")
    return parsed


def run(db: Session, path: str, *, force: bool = False) -> ImportReport:
    if not path:
        return ImportReport("not_configured", "ENGINE_OUTPUT_FILE is not set")
    source = Path(path)
    if not source.is_file():
        return ImportReport("missing", f"No engine output at {path} yet")

    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    parsed = parse(raw)

    existing = db.get(EngineRun, (parsed.as_of, parsed.engine_version))
    if existing is not None and existing.source_sha256 == digest and not force:
        return ImportReport("unchanged", f"{parsed.as_of} is already imported")

    # Replace rather than merge: a rerun for the same session supersedes the
    # earlier one whole, including any stock that fell out of the top five.
    db.execute(
        delete(EngineRecommendation).where(
            EngineRecommendation.as_of == parsed.as_of,
            EngineRecommendation.engine_version == parsed.engine_version,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.flush()
    db.add(
        EngineRun(
            as_of=parsed.as_of,
            engine_version=parsed.engine_version,
            generated_at=parsed.generated_at,
            source_sha256=digest,
            hold_backtest=[row.model_dump() for row in parsed.hold_backtest],
        )
    )
    for row in parsed.recommendations:
        db.add(
            EngineRecommendation(
                as_of=parsed.as_of,
                engine_version=parsed.engine_version,
                sid=row.sid,
                sector=row.sector,
                sector_rank=row.sector_rank,
                status=row.status,
                hold_state=row.hold_state,
                payload=row.model_dump(mode="json"),
            )
        )
    graded = _store_new_outcomes(db, parsed.outcomes)
    db.commit()
    scored = sum(row.status == "ok" for row in parsed.recommendations)
    logger.info("Imported engine run %s (%d rows)", parsed.as_of, len(parsed.recommendations))
    return ImportReport("imported", rows=len(parsed.recommendations), scored=scored, graded=graded)


def _store_new_outcomes(db: Session, outcomes: list[EngineOutcomeRow]) -> int:
    known = set(
        db.execute(
            select(
                EngineOutcome.call_as_of,
                EngineOutcome.engine_version,
                EngineOutcome.sid,
                EngineOutcome.horizon,
            )
        ).tuples()
    )
    added = 0
    for row in outcomes:
        key = (row.call_as_of, row.engine_version, row.sid, row.horizon)
        if key not in known:
            known.add(key)
            db.add(EngineOutcome(**row.model_dump()))
            added += 1
    return added


def latest_run(db: Session) -> EngineRun | None:
    return db.scalars(
        select(EngineRun).order_by(EngineRun.as_of.desc(), EngineRun.imported_at.desc()).limit(1)
    ).first()


def run_rows(db: Session, run_row: EngineRun, *, sector: str | None = None) -> list[EngineRecommendation]:
    query = select(EngineRecommendation).where(
        EngineRecommendation.as_of == run_row.as_of,
        EngineRecommendation.engine_version == run_row.engine_version,
    )
    if sector is not None:
        query = query.where(EngineRecommendation.sector == sector)
    return list(db.scalars(query.order_by(EngineRecommendation.sector, EngineRecommendation.sector_rank)))


def stock_row(db: Session, run_row: EngineRun, sid: str) -> EngineRecommendation | None:
    return db.get(EngineRecommendation, (run_row.as_of, run_row.engine_version, sid))
