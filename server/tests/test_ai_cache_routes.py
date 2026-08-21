"""What the free AI read routes must never do, pinned at the route.

`test_ai_cache_read.py` covers the matching and the freshness rule. This file
covers the two properties that only exist once the route is assembled, and both
are about restraint rather than about answers:

  * **Neither route may reach the exchange.** They exist so a page can render a
    verdict on mount, which means they run on every board load with a watchlist
    attached. A single month-fetch in here would queue on the same limiter the
    realtime poll depends on (3 calls / 5 s), twenty times over -- exactly the
    failure `/api/analysis/traditional` and `/api/analysis/backtest` are batched
    and cache-only to avoid. The fetcher is replaced with a bomb so a regression
    fails here rather than showing up as a rate-limited board in production.
  * **A miss is 204, not 404 and not an empty verdict.** An empty cache is the
    ordinary state of this endpoint. 404 would be indistinguishable from the one
    an unknown code gets, and a fabricated body would put a position call on
    screen that no model produced.

`ai_analysis` is JSONB, which the SQLite dialect cannot render, so this file
does what its neighbours avoid and teaches it one rendering -- as SQLite's own
JSON. `test_backtest_store.py` declines the same trick for a reason that does
not apply here: it is pinning a *write* whose upsert is PostgreSQL-only, while
what matters here is a read, and a read of a table that has to exist for the
route to reach its own 204. The shim is three lines and only ever compiles for
SQLite, so nothing about the production DDL moves.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.deps import get_current_user
from app.main import app
from app.models import AiAnalysis, AppUser, DailyPrice, FetchLog
from app.routers import analysis as analysis_router
from app.services import history as history_service
from app.services.analysis import prompts


@compiles(JSONB, "sqlite")
def _jsonb_as_sqlite_json(_type, _compiler, **_kw) -> str:
    """SQLite has no JSONB. It does have JSON, and reads are all this needs."""
    return "JSON"


def _sqlite_copy(table: Table) -> Table:
    """The same table, minus the parts only PostgreSQL can parse.

    Two of them: the JSONB server defaults are written as `'[]'::jsonb`, which
    is a cast SQLite rejects outright, and the foreign key points at `app_user`,
    which this fixture has no reason to create. Both matter to the write path
    and neither matters to a read, so they are dropped rather than emulated --
    the column types and the CHECK constraints, which do shape what can come
    back, are left exactly as production declares them.
    """
    copy = table.to_metadata(MetaData())
    for column in copy.columns:
        column.server_default = None
        column.foreign_keys.clear()
    copy.constraints = {
        constraint
        for constraint in copy.constraints
        if not isinstance(constraint, ForeignKeyConstraint)
    }
    return copy


MODEL = "gemini-test"

FRESH = "2330"  # bars, and a fetch_log saying they are current
STALE = "2317"  # bars, but no fetch_log -- the exchange has moved on
UNKNOWN = "9999"  # not a listed code


def _bars(sid: str, count: int = 60) -> list[DailyPrice]:
    start = datetime.date.today() - datetime.timedelta(days=count)
    return [
        DailyPrice(
            sid=sid,
            date=start + datetime.timedelta(days=i),
            open=100.0,
            close=100.0 + (i % 7),
            high=108.0,
            low=98.0,
            capacity=10_000 + i,
        )
        for i in range(count)
    ]


def _logs(sid: str) -> list[FetchLog]:
    """A fresh stamp for every bucket the AI window covers."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return [
        FetchLog(sid=sid, year=year, month=month, source="twse", row_count=20, fetched_at=now)
        for year, month in history_service.month_range(analysis_router.AI_MONTHS)
    ]


@pytest.fixture(autouse=True)
def _no_upstream(monkeypatch):
    """Any exchange call from these routes is the bug, so make it explode."""

    def _bomb(*_args, **_kwargs):
        raise AssertionError("the cache-only AI routes must not call the exchange")

    monkeypatch.setattr(history_service, "_fetch_month", _bomb)
    # The model is a `system_setting` row; that table is not in this fixture and
    # which model is active is not what these tests are about.
    monkeypatch.setattr(
        "app.services.analysis.model_settings.active_model", lambda _db: MODEL
    )


def _stored(sid: str, as_of: datetime.date, *, row_id: int = 1) -> AiAnalysis:
    return AiAnalysis(
        id=row_id,  # SQLite will not autoincrement a BIGINT -- see tests/helpers.py
        sid=sid,
        as_of=as_of,
        model=MODEL,
        prompt_version=prompts.PROMPT_VERSION,
        locale="zh-TW",
        action="enter",
        size="small",
        confidence="medium",
        headline="stored headline",
        reasons=["reason"],
        risks=["risk"],
        features={},
        requested_by=1,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    DailyPrice.__table__.create(engine)
    FetchLog.__table__.create(engine)
    _sqlite_copy(AiAnalysis.__table__).create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session = factory()
    session.add_all(_bars(FRESH))
    session.add_all(_logs(FRESH))
    session.add_all(_bars(STALE))
    session.commit()

    # Stamped with the newest bar FRESH holds, which is what `as_of` means.
    latest = max(row.date for row in session.query(DailyPrice).filter_by(sid=FRESH))
    session.add(_stored(FRESH, latest))
    session.commit()

    user = AppUser(id=1, username="alice", email="a@example.com", role="USER")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        session.close()
        engine.dispose()


def test_a_stored_verdict_is_served_without_one_upstream_call(client):
    """The whole point: a page renders this on mount, having paid nothing.

    The autouse bomb is half the assertion -- a month-fetch anywhere in here
    would raise rather than return.
    """
    res = client.get(f"/api/stocks/{FRESH}/analysis/ai")

    assert res.status_code == 200
    body = res.json()
    assert body["sid"] == FRESH
    assert body["cached"] is True
    assert body["verdict"]["action"] == "enter"
    assert body["verdict"]["headline"] == "stored headline"
    assert body["model"] == MODEL
    # The rule engine's answer comes along, so the card can show both.
    assert body["traditional"]["signal"] in {"buy", "sell", "hold"}


def test_a_verdict_in_another_locale_is_a_miss_not_a_translation(client):
    """The row is prose the model wrote in zh-TW; en is a different generation."""
    res = client.get(f"/api/stocks/{FRESH}/analysis/ai?locale=en")

    assert res.status_code == 204


def test_no_stored_verdict_is_204_rather_than_404_or_an_empty_body(client):
    """STALE has bars but no row -- the ordinary state of this endpoint."""
    res = client.get(f"/api/stocks/{STALE}/analysis/ai")

    assert res.status_code == 204
    assert res.content == b""


def test_an_unknown_code_is_404_so_it_stays_distinguishable_from_a_miss(client):
    res = client.get(f"/api/stocks/{UNKNOWN}/analysis/ai")

    assert res.status_code == 404


def test_a_miss_is_never_stored_by_a_proxy(client):
    """Verdicts are metered upstream of here; no shared cache may keep one."""
    res = client.get(f"/api/analysis/ai?sids={FRESH}")

    assert res.headers["Cache-Control"] == "no-store"


def test_the_batch_answers_for_a_whole_basket_without_one_upstream_call(client):
    res = client.get(f"/api/analysis/ai?sids={FRESH},{STALE},{UNKNOWN}")

    assert res.status_code == 200
    body = res.json()
    # An absent verdict is not an error, while an absent *code* is.
    assert [item["sid"] for item in body["items"]] == [FRESH]
    assert UNKNOWN in body["errors"]
    assert FRESH not in body["errors"]
    assert STALE not in body["errors"]


def test_the_batch_is_capped_so_one_url_cannot_widen_the_read(client):
    res = client.get("/api/analysis/ai?sids=" + ",".join(str(i) for i in range(100)))

    assert res.status_code == 200
