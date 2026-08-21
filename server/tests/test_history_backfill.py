"""Pins the two things that make an hourly exchange scrape safe to run.

The engine itself is a loop over `history_service.ensure_months`, which is
already tested. What is new, and what would hurt if it broke:

  * the job must not spend the shared exchange budget while the market is
    open, because the realtime poll queues on the same limiter;
  * it must stop at its budget rather than running to completion, and it must
    report progress honestly enough that an operator knows when to move it off
    the hourly schedule.
"""

from __future__ import annotations

import datetime

from app.services import history_backfill
from app.services.market_open import MARKET_TZ


def _at(day: str, hour: int) -> datetime.datetime:
    return datetime.datetime.fromisoformat(day).replace(hour=hour, tzinfo=MARKET_TZ)


# --- the quiet-hours guard ----------------------------------------------------


def test_a_weekday_session_is_market_hours():
    """2026-08-20 is a Thursday."""
    assert history_backfill.is_market_hours(_at("2026-08-20", 10)) is True
    assert history_backfill.is_market_hours(_at("2026-08-20", 13)) is True


def test_the_guard_widens_past_the_bell_at_both_ends():
    """The exchange runs 09:00-13:30; the margin covers the pre-open auction
    and the post-close reports the chip and index jobs pull."""
    assert history_backfill.is_market_hours(_at("2026-08-20", 8)) is True
    assert history_backfill.is_market_hours(_at("2026-08-20", 14)) is True
    assert history_backfill.is_market_hours(_at("2026-08-20", 7)) is False
    assert history_backfill.is_market_hours(_at("2026-08-20", 15)) is False


def test_weekends_are_always_quiet():
    """2026-08-22 is a Saturday -- the best time for a scrape."""
    assert history_backfill.is_market_hours(_at("2026-08-22", 10)) is False
    assert history_backfill.is_market_hours(_at("2026-08-23", 10)) is False


def test_an_open_market_spends_nothing_at_all(monkeypatch):
    """Not 'fetches less' -- fetches nothing, and never reaches the universe
    query either. An hourly job that ran through the session would sit in
    front of a user waiting for a quote."""
    monkeypatch.setattr(history_backfill, "is_market_hours", lambda *a: True)

    def _explode(*args, **kwargs):
        raise AssertionError("must not touch the database during market hours")

    monkeypatch.setattr(history_backfill, "universe", _explode)

    stats = history_backfill.run(db=None, years=10, stocks=300, month_budget=250)
    assert stats["skipped"] == 1
    assert stats["fetched"] == 0
    # -1 rather than 0: nothing was measured, and 0 would read as "all done".
    assert stats["remaining"] == -1


def test_the_manual_button_overrides_the_guard(monkeypatch):
    """An operator pressing run-now during a lunch break knows what they are
    asking for."""
    monkeypatch.setattr(history_backfill, "is_market_hours", lambda *a: True)
    monkeypatch.setattr(history_backfill, "universe", lambda *a, **k: [])
    monkeypatch.setattr(history_backfill, "_landed_months", lambda *a, **k: {})

    stats = history_backfill.run(
        db=None, years=10, stocks=300, month_budget=250, force=True
    )
    assert stats["skipped"] == 0


# --- the budget ---------------------------------------------------------------


class _Recorder:
    """Stands in for history_service, counting what was asked for."""

    def __init__(self, per_stock: int):
        self.per_stock = per_stock
        self.calls: list[str] = []

    def ensure_months(self, db, sid, buckets):  # noqa: ANN001, ARG002
        self.calls.append(sid)
        return [f"m{i}" for i in range(self.per_stock)], []


def _patch(monkeypatch, targets, recorder, landed=None):
    monkeypatch.setattr(history_backfill, "is_market_hours", lambda *a: False)
    monkeypatch.setattr(history_backfill, "universe", lambda *a, **k: targets)
    monkeypatch.setattr(
        history_backfill, "_landed_months", lambda *a, **k: landed or {}
    )
    monkeypatch.setattr(
        history_backfill.history_service, "ensure_months", recorder.ensure_months
    )


def test_the_run_stops_at_its_budget_rather_than_finishing_the_universe(monkeypatch):
    """The whole point of an hourly job: bounded runs, resumed next hour."""
    recorder = _Recorder(per_stock=120)
    _patch(monkeypatch, [f"{i:04d}" for i in range(50)], recorder)

    stats = history_backfill.run(db=None, years=10, stocks=50, month_budget=250)

    # 120 + 120 = 240, and the third stock would exceed 250.
    assert len(recorder.calls) == 3
    assert stats["fetched"] == 360
    assert stats["stocks"] == 3


def test_stocks_already_complete_are_never_fetched_again(monkeypatch):
    """Resumability is a property of the database, not of the run."""
    recorder = _Recorder(per_stock=120)
    done = {sid: 120 for sid in ("0001", "0002")}
    _patch(monkeypatch, ["0001", "0002", "0003"], recorder, landed=done)

    history_backfill.run(db=None, years=10, stocks=10, month_budget=250)
    assert recorder.calls == ["0003"]


def test_one_failing_stock_does_not_cost_the_rest_their_turn(monkeypatch):
    """A delisted or malformed code is a skip, not the end of the run."""
    recorder = _Recorder(per_stock=10)
    real = recorder.ensure_months

    def flaky(db, sid, buckets):  # noqa: ANN001
        if sid == "0002":
            raise RuntimeError("upstream said no")
        return real(db, sid, buckets)

    _patch(monkeypatch, ["0001", "0002", "0003"], recorder)
    monkeypatch.setattr(history_backfill.history_service, "ensure_months", flaky)

    stats = history_backfill.run(db=None, years=10, stocks=10, month_budget=250)
    assert stats["stocks"] == 2
    assert "0003" in recorder.calls


def test_remaining_is_re_read_rather_than_inferred(monkeypatch):
    """It is what an operator uses to decide when to move the job off hourly,
    so it has to mean "still incomplete now", not "skipped by this run"."""
    recorder = _Recorder(per_stock=120)
    targets = ["0001", "0002", "0003"]
    # One was already complete before the run; the state read afterwards says
    # two are, because this run finished another.
    reads = iter([{"0001": 120}, {"0001": 120, "0002": 120}])
    monkeypatch.setattr(history_backfill, "is_market_hours", lambda *a: False)
    monkeypatch.setattr(history_backfill, "universe", lambda *a, **k: targets)
    monkeypatch.setattr(history_backfill, "_landed_months", lambda *a, **k: next(reads))
    monkeypatch.setattr(
        history_backfill.history_service, "ensure_months", recorder.ensure_months
    )

    stats = history_backfill.run(db=None, years=10, stocks=10, month_budget=250)
    assert stats["complete"] == 2
    assert stats["remaining"] == 1


def test_the_budget_is_soft_and_a_run_finishes_the_stock_it_is_on(monkeypatch):
    """Documented rather than tightened, because it is the safer overshoot.

    Stopping mid-stock would leave a name half-fetched and the next run would
    have to work out where it got to. Finishing it costs at most one stock's
    worth beyond the budget -- which is why the setting has to be sized for
    `budget + years * 12 - 1`, not for the number typed into it.
    """
    recorder = _Recorder(per_stock=120)
    _patch(monkeypatch, ["0001", "0002"], recorder)

    stats = history_backfill.run(db=None, years=10, stocks=10, month_budget=12)

    # Asked for 12, fetched a whole stock -- and stopped before the second.
    assert stats["fetched"] == 120
    assert len(recorder.calls) == 1
