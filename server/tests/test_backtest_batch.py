"""Pins what the batch route promises beyond what `backtest.py` already does.

The engine is covered by `test_backtest.py`; the only new claims here are about
what happens to the stocks the engine *cannot* score, and they all fail
silently:

  * **A stock too thin to score stays in `items`.** Dropping it would leave
    `pooled` looking like it covered the whole basket. The caller asked about
    twenty stocks; a pooled rate secretly drawn from eleven is the wrong
    number reported with the right shape.
  * **It stays out of `pooled` too.** Contributing zero samples is harmless
    arithmetic, but contributing a `None` rate as a 0.0 would drag the pooled
    edge toward "the rule is terrible" for reasons that have nothing to do
    with the rule.
  * **An unresolvable code is an `errors` entry, not a dead batch.** Same
    contract the traditional batch and /api/realtime already use.

The route is cache-only by construction, and this fixture is what proves it:
there is no network here and no fetch stub, so a route that reached upstream
would fail rather than quietly spend the TWSE budget the realtime poll shares.
"""

from __future__ import annotations

import datetime
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import DailyPrice
from app.services.analysis import backtest

SCORED = "2330"  # given enough bars to produce a verdict
THIN = "2317"  # given too few, on purpose
UNKNOWN = "9999"  # not a listed code


def _bars(sid: str, count: int, seed: int) -> list[DailyPrice]:
    """A random walk on consecutive days. Direction is irrelevant here --
    the assertions are about coverage, never about the rates themselves.
    """
    rng = random.Random(seed)
    start = datetime.date(2025, 1, 6)
    price = 100.0
    rows = []
    for i in range(count):
        open_ = price
        close = max(1.0, price * (1 + rng.uniform(-0.05, 0.05)))
        rows.append(
            DailyPrice(
                sid=sid,
                date=start + datetime.timedelta(days=i),
                open=round(open_, 2),
                close=round(close, 2),
                high=round(max(open_, close), 2),
                low=round(min(open_, close), 2),
                capacity=rng.randint(1_000, 50_000),
            )
        )
        price = close
    return rows


@pytest.fixture
def client():
    """SQLite holding only `daily_price` -- the one table this route reads.

    Same trick `conftest.db` uses for the account tables: the rest of the
    schema is PostgreSQL JSONB, so `create_all` is not available.
    """
    # TestClient serves the app on its own thread, so the default per-thread
    # SQLite connection (and the per-connection :memory: database that comes
    # with it) would hand the request an empty schema.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    DailyPrice.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session = factory()
    session.add_all(_bars(SCORED, 200, seed=1))
    session.add_all(_bars(THIN, backtest.MIN_BARS_FOR_BACKTEST - 1, seed=2))
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _get(client, sids: str, months: int = 24):
    # 24 months so the fixture's 2025 dates fall inside the window the route
    # asks the database for; the walk is dated, not relative to today.
    response = client.get(f"/api/analysis/backtest?sids={sids}&months={months}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_stock_too_thin_to_score_is_listed_rather_than_dropped(client):
    body = _get(client, f"{SCORED},{THIN}")

    listed = {item["sid"]: item for item in body["items"]}
    assert set(listed) == {SCORED, THIN}

    thin = listed[THIN]
    assert thin["note"], "a stock that could not be scored must say why"
    assert thin["start"] is None
    assert thin["buy_stats"] == []
    assert thin["baseline"] == []
    assert thin["strategy_return"] is None


def test_pooled_counts_only_the_stocks_it_actually_scored(client):
    body = _get(client, f"{SCORED},{THIN}")

    assert body["pooled"], "the scorable stock should still produce a pool"
    for entry in body["pooled"]:
        assert entry["stocks"] == 1  # not 2: THIN contributed nothing


def test_pooled_is_empty_when_nothing_could_be_scored(client):
    body = _get(client, THIN)

    assert body["pooled"] == []
    assert len(body["items"]) == 1
    assert body["items"][0]["note"]


def test_an_unknown_code_becomes_an_error_without_dropping_the_batch(client):
    body = _get(client, f"{SCORED},{UNKNOWN}")

    assert UNKNOWN in body["errors"]
    assert [item["sid"] for item in body["items"]] == [SCORED]
    assert body["pooled"]


def test_every_scored_stock_reports_a_baseline_beside_its_hit_rate(client):
    """The pairing is the whole point: a win rate shipped without the base
    rate of the same window is a number the reader cannot evaluate.
    """
    body = _get(client, SCORED)
    item = body["items"][0]

    horizons = {h for h in backtest.HORIZONS}
    assert {b["horizon"] for b in item["baseline"]} == horizons
    assert {e["horizon"] for e in item["edges"]} == horizons
    assert {s["horizon"] for s in item["buy_stats"]} == horizons

    baseline = {b["horizon"]: b for b in item["baseline"]}
    buys = {s["horizon"]: s for s in item["buy_stats"]}
    edges = {e["horizon"]: e for e in item["edges"]}

    for horizon in horizons:
        assert baseline[horizon]["samples"] > 0
        if buys[horizon]["win_rate"] is None:
            assert edges[horizon]["buy_edge"] is None
        else:
            assert edges[horizon]["buy_edge"] == pytest.approx(
                buys[horizon]["win_rate"] - baseline[horizon]["up_rate"]
            )
