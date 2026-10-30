"""Pins the engine import and who may read its output.

Two failures here would be quiet. A route open to anonymous visitors would
publish buy prices, which is a licensing question rather than a bug report. And
a half-applied or misread file would put wrong prices on a page that looks fine.
"""

from __future__ import annotations

import datetime
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.deps import get_current_user
from app.main import app
from app.models import EngineOutcome, EngineRecommendation, EngineRun
from app.services import engine_import
from app.services.jobs import handlers
from app.services.jobs.registry import JobContext


@pytest.fixture
def engine_db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EngineRun.__table__.create(engine)
    EngineRecommendation.__table__.create(engine)
    EngineOutcome.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _row(sid="2330", sector="半導體", rank=1, hold_state="strong", **overrides):
    entry = {
        "k_atr": 0,
        "price": 2370.0,
        "ev": 0.03,
        "ev_lo": 0.01,
        "win_rate": 0.6,
        "worst_5pct": -0.18,
        "fill_rate": 1.0,
        "max_weight": 0.03,
    }
    plan = {"ev": 0.03, "worst_5pct": -0.18, "trust": "low", "entry": entry}
    return {
        "sid": sid,
        "name": "台積電",
        "market": "上市",
        "sector": sector,
        "sector_rank": rank,
        "market_cap": 6.2e13,
        "status": "ok",
        "status_detail": None,
        "close": 2370.0,
        "as_of": "2026-10-30",
        "short": plan,
        "long": plan,
        "hold_state": hold_state,
        "risk_line": 2250.0,
        **overrides,
    }


def _file(tmp_path, *, as_of="2026-10-30", rows=None, **overrides):
    payload = {
        "schema_version": 1,
        "engine_version": "1.0.0",
        "generated_at": "2026-10-30T12:00:00+00:00",
        "as_of": as_of,
        "recommendations": rows if rows is not None else [_row()],
        "hold_backtest": [
            {
                "state": "weak",
                "samples": 100,
                "excess_20d": 0.01,
                "excess_60d": 0.05,
                "excess_60d_lo": 0.01,
                "excess_60d_hi": 0.08,
                "up_rate_60d": 0.6,
            }
        ],
        **overrides,
    }
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_import_stores_the_rows(engine_db, tmp_path):
    report = engine_import.run(engine_db, _file(tmp_path, rows=[_row(), _row(sid="2454", rank=2, status="failed")]))
    assert (report.status, report.rows, report.scored) == ("imported", 2, 1)
    assert len(list(engine_db.scalars(select(EngineRecommendation)))) == 2


def test_same_file_is_not_imported_twice(engine_db, tmp_path):
    path = _file(tmp_path)
    engine_import.run(engine_db, path)
    assert engine_import.run(engine_db, path).status == "unchanged"


def test_rerun_for_a_session_replaces_it_whole(engine_db, tmp_path):
    engine_import.run(engine_db, _file(tmp_path, rows=[_row(), _row(sid="2454", rank=2)]))
    engine_import.run(engine_db, _file(tmp_path, rows=[_row(hold_state="weak")]))
    rows = list(engine_db.scalars(select(EngineRecommendation)))
    assert [(r.sid, r.hold_state) for r in rows] == [("2330", "weak")]


def test_a_new_session_keeps_yesterdays_rows(engine_db, tmp_path):
    """The accuracy check depends on old calls surviving."""
    engine_import.run(engine_db, _file(tmp_path, as_of="2026-10-29"))
    engine_import.run(engine_db, _file(tmp_path, as_of="2026-10-30"))
    assert len(list(engine_db.scalars(select(EngineRecommendation)))) == 2
    assert engine_import.latest_run(engine_db).as_of == datetime.date(2026, 10, 30)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 99},
        {"rows": []},
        {"rows": [_row(hold_state="sell_now")]},
        {"rows": [{"sid": "2330"}]},
    ],
)
def test_a_bad_file_changes_nothing(engine_db, tmp_path, overrides):
    engine_import.run(engine_db, _file(tmp_path, as_of="2026-10-29"))
    with pytest.raises(engine_import.EngineFileError):
        engine_import.run(engine_db, _file(tmp_path, **overrides))
    assert engine_import.latest_run(engine_db).as_of == datetime.date(2026, 10, 29)


def test_no_file_and_no_config_are_not_failures(engine_db, tmp_path):
    assert engine_import.run(engine_db, "").status == "not_configured"
    assert engine_import.run(engine_db, str(tmp_path / "none.json")).status == "missing"


def test_job_reports_a_skip_when_nothing_is_new(engine_db, monkeypatch):
    monkeypatch.setattr(handlers.settings, "engine_output_file", "")
    result = handlers.engine_import(JobContext(db=engine_db, trigger="schedule", force=False, freshness_seconds=0))
    assert result.status == "skipped"


@pytest.fixture
def client(engine_db):
    app.dependency_overrides[get_db] = lambda: engine_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "path",
    ["/api/engine/overview", "/api/engine/recommendations?sector=半導體", "/api/engine/stocks/2330"],
)
def test_every_route_requires_sign_in(client, path):
    assert client.get(path).status_code == 401


@pytest.fixture
def signed_in(client):
    app.dependency_overrides[get_current_user] = lambda: object()
    return client


def test_empty_database_is_an_empty_answer(signed_in):
    assert signed_in.get("/api/engine/overview").json()["sectors"] == []
    assert signed_in.get("/api/engine/recommendations?sector=半導體").json() == []
    assert signed_in.get("/api/engine/stocks/2330").json()["item"] is None


def test_overview_counts_each_sector(signed_in, engine_db, tmp_path):
    waiting = _row(sid="2454", rank=2, hold_state="weak", short={"ev": 0.01, "worst_5pct": -0.2, "trust": "low", "entry": None})
    engine_import.run(engine_db, _file(tmp_path, rows=[_row(), waiting]))
    body = signed_in.get("/api/engine/overview").json()
    assert body["sectors"] == [{"sector": "半導體", "stocks": 2, "short_now": 1, "long_now": 2, "weak": 1}]
    assert body["hold_backtest"][0]["state"] == "weak"


def test_sector_list_is_ranked_and_stock_lookup_works(signed_in, engine_db, tmp_path):
    engine_import.run(engine_db, _file(tmp_path, rows=[_row(sid="2454", rank=2), _row(), _row(sid="1101", sector="水泥")]))
    rows = signed_in.get("/api/engine/recommendations?sector=半導體").json()
    assert [r["sid"] for r in rows] == ["2330", "2454"]
    assert signed_in.get("/api/engine/stocks/1101").json()["item"]["sector"] == "水泥"
    assert signed_in.get("/api/engine/stocks/9999").json()["item"] is None


def test_old_data_is_flagged(signed_in, engine_db, tmp_path):
    engine_import.run(engine_db, _file(tmp_path, as_of="2020-01-02"))
    assert signed_in.get("/api/engine/overview").json()["stale"] is True


def _outcome(day, *, sid="2330", horizon="short", call="buy_now", hold_state="strong", ret=0.05, bench=0.02):
    return {
        "call_as_of": (datetime.date(2026, 1, 1) + datetime.timedelta(days=day)).isoformat(),
        "engine_version": "1.0.0",
        "sid": sid,
        "sector": "半導體",
        "horizon": horizon,
        "call": call,
        "hold_state": hold_state,
        "filled": call != "no_buy",
        "stopped": False,
        "followed_return": None if call == "no_buy" else ret,
        "market_entry_return": ret,
        "benchmark_return": bench,
    }


def _import_outcomes(db, tmp_path, outcomes):
    return engine_import.run(db, _file(tmp_path, schema_version=2, outcomes=outcomes), force=True)


def _call(body, horizon, call):
    return next(c for c in body["calls"] if c["horizon"] == horizon and c["call"] == call)


def test_first_grade_wins(engine_db, tmp_path):
    """A later copy can differ after Yahoo restates prices. The record must not."""
    assert _import_outcomes(engine_db, tmp_path, [_outcome(0)]).graded == 1
    assert _import_outcomes(engine_db, tmp_path, [_outcome(0, ret=-0.5)]).graded == 0
    assert engine_db.scalars(select(EngineOutcome)).one().market_entry_return == 0.05


def test_old_file_without_outcomes_still_imports(engine_db, tmp_path):
    assert engine_import.run(engine_db, _file(tmp_path)).graded == 0


def test_track_record_requires_sign_in(client):
    assert client.get("/api/engine/track-record").status_code == 401


def test_no_figures_before_enough_call_dates(signed_in, engine_db, tmp_path):
    """Many stocks on one day are one market mood, however many rows they make."""
    many = [_outcome(day, sid=str(sid)) for day in range(5) for sid in range(50)]
    _import_outcomes(engine_db, tmp_path, many)
    row = _call(signed_in.get("/api/engine/track-record").json(), "short", "buy_now")
    assert row["calls"] == 250
    assert row["ready"] is False and row["avg_return"] is None and row["conclusion"] == "collecting"


def test_average_appears_but_no_conclusion_until_periods_accumulate(signed_in, engine_db, tmp_path):
    _import_outcomes(engine_db, tmp_path, [_outcome(day) for day in range(30)])
    row = _call(signed_in.get("/api/engine/track-record").json(), "short", "buy_now")
    assert row["ready"] is True and row["avg_excess"] == pytest.approx(0.03)
    assert row["conclusion"] == "collecting"


def test_conclusion_once_enough_independent_periods(signed_in, engine_db, tmp_path):
    rows = [_outcome(day) for day in range(170)]
    rows += [_outcome(day, sid="2454", call="no_buy", ret=-0.03) for day in range(170)]
    _import_outcomes(engine_db, tmp_path, rows)
    body = signed_in.get("/api/engine/track-record").json()
    assert _call(body, "short", "buy_now")["conclusion"] == "beat"
    assert _call(body, "short", "no_buy")["conclusion"] == "lagged"


def test_hold_grades_use_only_the_long_horizon(signed_in, engine_db, tmp_path):
    rows = [_outcome(day, horizon="short", hold_state="weak") for day in range(30)]
    _import_outcomes(engine_db, tmp_path, rows)
    body = signed_in.get("/api/engine/track-record").json()
    assert next(h for h in body["holds"] if h["hold_state"] == "weak")["calls"] == 0
