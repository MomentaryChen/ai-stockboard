"""Pins the free read path -- the one that must never spend anything.

`get_or_create` is metered and answers on POST. These are its unmetered
siblings, and the failures worth pinning are the ones that still render as a
confident verdict:

  * **A verdict must be for the day it says it is.** The basket lookup asks for
    `as_of IN (every day in the basket)`, because different stocks end on
    different bars. That union is what makes one query enough, and it is also
    what lets a row belonging to 2330 Friday come back while matching 0050
    Thursday. Nothing downstream could tell -- the card would render a real
    verdict, correctly formatted, about the wrong session.
  * **Stale bars must not be judged at all.** The read path deliberately never
    calls the exchange, so "we hold bars" and "we hold current bars" are
    different questions. Answering the second with the first would serve
    Thursday's call all through Friday afternoon.
  * **A stock with too little history is absent, not an error.** A twenty-sid
    board contains newly listed stocks, and one of them raising would empty
    the board.

No database: the write path is JSONB and PostgreSQL-only, same as
`test_backtest_store.py`. What is exercised here is the matching and the
freshness rule, so the session is a stub that hands back the rows a real query
would have.
"""

from __future__ import annotations

import datetime

import pytest

from app.models import AiAnalysis, DailyPrice, FetchLog
from app.services import history as history_service
from app.services.analysis import ai, prompts

START = datetime.date(2024, 1, 2)
MODEL = "gemini-test"


def _rows(sid: str, n: int = 40, *, end_offset: int = 0) -> list[DailyPrice]:
    """`n` consecutive daily bars, optionally stopping `end_offset` days early."""
    return [
        DailyPrice(
            sid=sid,
            date=START + datetime.timedelta(days=i),
            open=50.0,
            close=50.0 + (i % 5),
            high=56.0,
            low=48.0,
            capacity=10_000 + i,
        )
        for i in range(n - end_offset)
    ]


def _stored(
    sid: str,
    as_of: datetime.date,
    locale: str = "zh-TW",
    depth: str = "quick",
    features: dict | None = None,
) -> AiAnalysis:
    # `depth` is stated rather than left to the column default: that default is
    # applied on insert, and these rows never reach a database.
    return AiAnalysis(
        sid=sid,
        as_of=as_of,
        model=MODEL,
        prompt_version=ai.prompt_version(depth),
        locale=locale,
        depth=depth,
        action="enter",
        size="small",
        confidence="medium",
        headline=f"{sid} headline",
        reasons=["r"],
        risks=["k"],
        features=features or {},
        created_at=datetime.datetime(2024, 3, 1, tzinfo=datetime.timezone.utc),
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _StubSession:
    """Answers one kind of query with a fixed set of rows, and counts calls."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = 0

    def execute(self, _stmt):
        self.executed += 1
        return _Result(self._rows)


@pytest.fixture(autouse=True)
def _pin_model(monkeypatch):
    """The active model is a database setting; the cache key is what matters."""
    monkeypatch.setattr(
        "app.services.analysis.model_settings.active_model", lambda _db: MODEL
    )


# --- the basket lookup -------------------------------------------------------


def test_a_row_for_another_sids_trading_day_is_not_served_as_this_ones():
    """The `as_of IN (...)` union is per-basket, so the match must be per-sid.

    2330 trades through Friday; 0050 stops on Thursday, so the query asks for
    `as_of IN (Thursday, Friday)` across both sids. A stale 0050 row stamped
    Friday -- left over from before a bar was corrected away, or from a basket
    that once held a different day -- satisfies that clause. Serving it would
    put a real, correctly formatted verdict on the card under a session 0050
    does not have, and nothing downstream could tell.
    """
    rows_by_sid = {"2330": _rows("2330"), "0050": _rows("0050", end_offset=1)}
    friday = rows_by_sid["2330"][-1].date
    thursday = rows_by_sid["0050"][-1].date
    assert friday != thursday

    # Both rows satisfy the IN clause; only one of them is about its own sid's
    # latest bar.
    db = _StubSession([_stored("2330", friday), _stored("0050", friday)])
    out = ai.get_cached_many(
        db, names={"2330": "TSMC", "0050": "ETF"}, rows_by_sid=rows_by_sid
    )

    assert [(r.sid, r.as_of) for r in out] == [("2330", friday)]


def test_a_sid_is_served_its_own_day_when_the_basket_spans_two():
    """The other half of the same rule: Thursday's row is right for 0050."""
    rows_by_sid = {"2330": _rows("2330"), "0050": _rows("0050", end_offset=1)}
    friday = rows_by_sid["2330"][-1].date
    thursday = rows_by_sid["0050"][-1].date

    db = _StubSession([_stored("2330", friday), _stored("0050", thursday)])
    out = ai.get_cached_many(
        db, names={"2330": "TSMC", "0050": "ETF"}, rows_by_sid=rows_by_sid
    )

    assert sorted((r.sid, r.as_of) for r in out) == sorted(
        [("2330", friday), ("0050", thursday)]
    )


def test_the_basket_is_one_query_however_many_sids_it_carries():
    """The point of the route. One query, or it is twenty requests in a coat."""
    rows_by_sid = {f"{i:04d}": _rows(f"{i:04d}") for i in range(12)}
    db = _StubSession([])

    ai.get_cached_many(
        db, names={sid: sid for sid in rows_by_sid}, rows_by_sid=rows_by_sid
    )

    assert db.executed == 1


def test_a_sid_with_too_little_history_is_dropped_rather_than_raising():
    """`get_or_create` raises InsufficientData here; the board must not."""
    rows_by_sid = {"2330": _rows("2330"), "6666": _rows("6666", n=4)}
    db = _StubSession([_stored("2330", rows_by_sid["2330"][-1].date)])

    out = ai.get_cached_many(
        db, names={"2330": "TSMC", "6666": "New"}, rows_by_sid=rows_by_sid
    )

    assert [r.sid for r in out] == ["2330"]


def test_get_cached_returns_none_for_too_little_history_without_touching_the_db():
    """The check is before the lookup, so a cold sid costs no query at all."""
    db = _StubSession([])

    assert ai.get_cached(db, sid="6666", name="New", rows=_rows("6666", n=4)) is None
    assert db.executed == 0


def test_get_cached_reports_a_hit_as_cached():
    rows = _rows("2330")
    db = _StubSession([_stored("2330", rows[-1].date)])

    result = ai.get_cached(db, sid="2330", name="TSMC", rows=rows)

    assert result is not None
    assert result.cached is True
    assert result.as_of == rows[-1].date
    # The rule engine's answer rides along, same as on the metered path -- the
    # card puts the two verdicts side by side and cannot do it with one.
    assert result.traditional is not None


def test_an_unknown_locale_falls_back_before_the_lookup_rather_than_missing():
    """Otherwise 'de' would key a permanent miss and re-offer a paid button."""
    rows = _rows("2330")
    db = _StubSession([_stored("2330", rows[-1].date)])

    result = ai.get_cached(db, sid="2330", name="TSMC", rows=rows, locale="de")

    assert result is not None
    assert result.locale == ai.DEFAULT_LOCALE


# --- the freshness gate ------------------------------------------------------


def _log(
    sid: str, year: int, month: int, *, age_seconds: float, rows: int = 20
) -> FetchLog:
    now = datetime.datetime.now(datetime.timezone.utc)
    return FetchLog(
        sid=sid,
        year=year,
        month=month,
        source="twse",
        row_count=rows,
        fetched_at=now - datetime.timedelta(seconds=age_seconds),
    )


def _this_month() -> tuple[int, int]:
    today = datetime.date.today()
    return today.year, today.month


def test_a_sid_missing_a_bucket_is_not_treated_as_cached():
    """`read_prices` would answer, with a hole in it. That is the trap."""
    buckets = history_service.month_range(3)
    db = _StubSession([_log("2330", *buckets[0], age_seconds=0)])

    assert history_service.months_are_cached(db, "2330", buckets) is False
    assert history_service.cached_sids(db, ["2330"], buckets) == set()


def test_a_stale_current_month_is_not_cached_however_old_the_rest_is():
    """The exchange has published since; a verdict now would be for yesterday."""
    buckets = history_service.month_range(3)
    stale = history_service.settings.current_month_ttl_seconds + 60
    logs = [_log("2330", y, m, age_seconds=0) for y, m in buckets[:-1]]
    logs.append(_log("2330", *_this_month(), age_seconds=stale))

    db = _StubSession(logs)

    assert history_service.months_are_cached(db, "2330", buckets) is False


def test_every_bucket_fresh_is_the_only_way_through():
    buckets = history_service.month_range(3)
    logs = [_log("2330", y, m, age_seconds=0) for y, m in buckets]

    db = _StubSession(logs)

    assert history_service.months_are_cached(db, "2330", buckets) is True
    assert history_service.cached_sids(db, ["2330"], buckets) == {"2330"}


def test_cached_sids_separates_the_basket_and_reads_it_once():
    """One stale sid must not disqualify the rest, and must not cost a query."""
    buckets = history_service.month_range(3)
    logs = [_log("2330", y, m, age_seconds=0) for y, m in buckets]
    logs += [_log("0050", y, m, age_seconds=0) for y, m in buckets[:-1]]

    db = _StubSession(logs)

    assert history_service.cached_sids(db, ["2330", "0050"], buckets) == {"2330"}
    assert db.executed == 1


def test_an_empty_basket_asks_nothing():
    db = _StubSession([])

    assert history_service.cached_sids(db, [], history_service.month_range(3)) == set()
    assert db.executed == 0
