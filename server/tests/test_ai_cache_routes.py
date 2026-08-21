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
from app.models import (
    AiAnalysis,
    AiHoldAnalysis,
    AppUser,
    DailyPrice,
    DividendEvent,
    DividendFetchLog,
    FetchLog,
    FundamentalsAnnual,
    ValuationDay,
)
from app.routers import analysis as analysis_router
from app.services import dividend as dividend_service
from app.services import history as history_service
from app.services.analysis import ai as ai_service
from app.services.analysis import gemini, hold_ai as hold_ai_service
from app.services.analysis import hold_gemini, prompts


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


def _stored(
    sid: str,
    as_of: datetime.date,
    *,
    row_id: int = 1,
    depth: str = "quick",
    headline: str = "stored headline",
) -> AiAnalysis:
    # `depth` is stated rather than left to the column default: `_sqlite_copy`
    # drops the server defaults, so an unstated depth would be inserted NULL and
    # match neither lane.
    return AiAnalysis(
        id=row_id,  # SQLite will not autoincrement a BIGINT -- see tests/helpers.py
        sid=sid,
        as_of=as_of,
        model=MODEL,
        prompt_version=ai_service.prompt_version(depth),
        locale="zh-TW",
        depth=depth,
        action="enter",
        size="small",
        confidence="medium",
        headline=headline,
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


# --- the two depths are two answers ------------------------------------------
#
# The bug these pin: the panel's 價量 and 深度 controls both went through a cache
# lookup that named no depth, so whichever verdict existed answered whichever
# button was pressed. Two controls, one answer, and the other one unreachable.


def _seed_deep(client) -> datetime.date:
    """A deep verdict beside the quick one FRESH already has."""
    session = app.dependency_overrides[get_db]()
    latest = max(row.date for row in session.query(DailyPrice).filter_by(sid=FRESH))
    session.add(_stored(FRESH, latest, row_id=2, depth="deep", headline="deep headline"))
    session.commit()
    return latest


def test_the_free_read_pins_the_depth_it_was_asked_for(client):
    """Each lane serves its own verdict, and only its own."""
    _seed_deep(client)

    quick = client.get(f"/api/stocks/{FRESH}/analysis/ai?depth=quick")
    deep = client.get(f"/api/stocks/{FRESH}/analysis/ai?depth=deep")

    assert quick.json()["depth"] == "quick"
    assert quick.json()["verdict"]["headline"] == "stored headline"
    assert deep.json()["depth"] == "deep"
    assert deep.json()["verdict"]["headline"] == "deep headline"


def test_an_unpinned_read_still_serves_the_better_informed_verdict(client):
    """What a board wants from one read: nobody is charged for either, so
    showing the shallower of two paid-for answers would be worse for free."""
    _seed_deep(client)

    res = client.get(f"/api/stocks/{FRESH}/analysis/ai")

    assert res.json()["depth"] == "deep"


def test_a_depth_nobody_paid_for_is_204_rather_than_the_other_one(client):
    """FRESH has only a quick verdict. Falling back to it would put a price-only
    call on screen under the heading of an assessment that reads chip flow."""
    res = client.get(f"/api/stocks/{FRESH}/analysis/ai?depth=deep")

    assert res.status_code == 204


def test_the_metered_route_probes_its_own_depth(client, monkeypatch):
    """The POST's cache probe is in front of the money. Unpinned, pressing
    價量評估 on a deeply analysed stock returned the deep row -- free, instant,
    and answering a question nobody asked."""
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    # Reaching the provider means the probe missed, which is the other half of
    # this assertion: a hit must be a hit for *this* depth.
    monkeypatch.setattr(
        gemini,
        "generate",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should have hit cache")),
    )
    _seed_deep(client)

    res = client.post(f"/api/stocks/{FRESH}/analysis/ai?depth=quick")

    assert res.status_code == 200
    assert res.json()["depth"] == "quick"
    assert res.json()["verdict"]["headline"] == "stored headline"


# --- the 存股 lane's free read ------------------------------------------------
#
# It arrived a release after the technical one, and the deep assessment is what
# made it necessary: two verdicts a reader is meant to compare are worth nothing
# if reaching either costs a generation. The properties are the same two --
# never reach the exchange, and never answer for a depth nobody paid for.


def _hold_stored(sid: str, as_of: datetime.date, *, depth: str, headline: str) -> AiHoldAnalysis:
    return AiHoldAnalysis(
        id=1 if depth == "quick" else 2,
        sid=sid,
        as_of=as_of,
        model=MODEL,
        prompt_version=hold_ai_service.prompt_version(depth),
        locale="zh-TW",
        depth=depth,
        suitability="ok",
        confidence="medium",
        headline=headline,
        reasons=["reason"],
        risks=["risk"],
        agrees_with_rules=None,
        features={},
        requested_by=1,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )


@pytest.fixture
def hold_client(monkeypatch):
    """The 存股 snapshot's tables, and a bomb on every fetching sibling.

    Wider than the `client` fixture because the snapshot behind this route is
    the widest cache-only read in the service: bars, the payout archive, annual
    figures and the valuation band. Every one of those has a fetching sibling
    one call away, which is exactly why the route is worth pinning.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        DailyPrice.__table__,
        FetchLog.__table__,
        DividendEvent.__table__,
        DividendFetchLog.__table__,
        FundamentalsAnnual.__table__,
        ValuationDay.__table__,
    ):
        # Created as production declares them. Only the verdict table needs the
        # `_sqlite_copy` treatment, because only it carries JSONB and a foreign
        # key this fixture has no reason to satisfy -- and stripping the server
        # defaults off the others would drop the `updated_at` every one of them
        # relies on.
        table.create(engine)
    _sqlite_copy(AiHoldAnalysis.__table__).create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session = factory()
    session.add_all(_bars(FRESH))
    session.add_all(_logs(FRESH))
    session.commit()

    latest = max(row.date for row in session.query(DailyPrice).filter_by(sid=FRESH))
    session.add(_hold_stored(FRESH, latest, depth="quick", headline="checklist verdict"))
    session.commit()

    def _bomb(*_args, **_kwargs):
        raise AssertionError("the free 存股 read must not call the exchange")

    monkeypatch.setattr(dividend_service, "get_dividends", _bomb, raising=False)

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


def test_a_stored_hold_verdict_is_served_without_one_upstream_call(hold_client):
    res = hold_client.get(f"/api/stocks/{FRESH}/analysis/ai-hold")

    assert res.status_code == 200
    body = res.json()
    assert body["depth"] == "quick"
    assert body["cached"] is True
    assert body["verdict"]["headline"] == "checklist verdict"
    # The checklist rides along, so the card can put the deterministic verdict
    # beside the generated one rather than making the reader fetch it again.
    assert "dimensions" in body["rules"]
    assert body["features"]["sid"] == FRESH
    # A quick verdict has no deep block, which is what keeps its disclaimer
    # honest about what the model was shown.
    assert body["deep"] is None


def test_the_hold_read_pins_the_depth_it_was_asked_for(hold_client):
    """FRESH has only the checklist verdict. Serving it under the 深度 heading
    would put an assessment that never saw a payout series on screen as one
    that did."""
    res = hold_client.get(f"/api/stocks/{FRESH}/analysis/ai-hold?depth=deep")

    assert res.status_code == 204


def test_an_unpinned_hold_read_serves_the_better_informed_verdict(hold_client):
    session = app.dependency_overrides[get_db]()
    latest = max(row.date for row in session.query(DailyPrice).filter_by(sid=FRESH))
    session.add(_hold_stored(FRESH, latest, depth="deep", headline="deep verdict"))
    session.commit()

    res = hold_client.get(f"/api/stocks/{FRESH}/analysis/ai-hold")

    assert res.json()["depth"] == "deep"
    assert res.json()["verdict"]["headline"] == "deep verdict"


def test_a_company_with_no_hold_verdict_is_204(hold_client):
    res = hold_client.get(f"/api/stocks/{STALE}/analysis/ai-hold")

    assert res.status_code == 204
    assert res.content == b""


def test_the_metered_hold_route_probes_its_own_depth(hold_client, monkeypatch):
    """The same failure the technical lane had: an unpinned probe in front of
    the meter hands back the other depth's answer, free and mislabelled."""
    monkeypatch.setattr(hold_gemini, "is_configured", lambda: True)
    monkeypatch.setattr(
        hold_gemini,
        "generate",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should have hit cache")),
    )
    session = app.dependency_overrides[get_db]()
    latest = max(row.date for row in session.query(DailyPrice).filter_by(sid=FRESH))
    session.add(_hold_stored(FRESH, latest, depth="deep", headline="deep verdict"))
    session.commit()

    res = hold_client.post(f"/api/stocks/{FRESH}/analysis/ai-hold?depth=quick")

    assert res.status_code == 200
    assert res.json()["depth"] == "quick"
    assert res.json()["verdict"]["headline"] == "checklist verdict"
