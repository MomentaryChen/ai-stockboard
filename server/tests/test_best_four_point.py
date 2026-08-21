"""Pins the two twstock porting defects that `_GrsBestFourPoint` repairs.

The README's 20 000-sequence experiment is the motivation, not the test: CI
does not need that many draws to catch a regression. What it does need is

  * a deterministic pair of bars that distinguish close-vs-close from
    close-vs-open (defect 2),
  * the proof that twstock's `bias_ratio()` is a non-empty tuple, hence
    unconditionally truthy (defect 1),
  * and that our override returns that tuple's boolean, not the tuple.

If someone "simplifies" the three overrides, or a twstock bump changes the
return type, these fail before the side-by-side on the stock page starts lying.
"""

from __future__ import annotations

import datetime
import random

from twstock.analytics import BestFourPoint

from app.models import DailyPrice
from app.services.analysis.traditional import (
    MIN_SAMPLES_FOR_BFP,
    _CachedStock,
    _GrsBestFourPoint,
    best_four_point,
)

SID = "2330"


def _bar(
    day: datetime.date,
    *,
    open_: float,
    close: float,
    capacity: int,
    high: float | None = None,
    low: float | None = None,
) -> DailyPrice:
    return DailyPrice(
        sid=SID,
        date=day,
        open=open_,
        close=close,
        high=high if high is not None else max(open_, close),
        low=low if low is not None else min(open_, close),
        capacity=capacity,
    )


def _stock(rows: list[DailyPrice]) -> _CachedStock:
    return _CachedStock(rows)


def _flat_prefix(n: int, start: datetime.date) -> list[DailyPrice]:
    """Quiet days so MA / 量-價 rules on the last two bars are the only movers."""
    return [
        _bar(
            start + datetime.timedelta(days=i),
            open_=50.0,
            close=50.0,
            capacity=10_000,
        )
        for i in range(n)
    ]


def test_grs_bias_ratio_returns_the_boolean_from_the_tuple():
    """Defect 1: twstock dropped the `[0]`; a 3-tuple is always True."""
    rows = _flat_prefix(MIN_SAMPLES_FOR_BFP, datetime.date(2024, 1, 1))
    stock = _stock(rows)
    twstock = BestFourPoint(stock)
    grs = _GrsBestFourPoint(stock)

    raw = twstock.bias_ratio()
    assert isinstance(raw, tuple)
    assert len(raw) == 3
    assert raw  # non-empty tuple: the gate that never fires
    assert isinstance(grs.bias_ratio(), bool)
    assert grs.bias_ratio() is raw[0]


def test_grs_volume_shrink_compares_close_to_previous_close_not_open():
    """Defect 2: yesterday's long red makes a pullback look like 價不跌 in twstock.

    Yesterday: open 10, close 20 (long red). Today: volume down, close 15.
    15 is below yesterday's close (actually 價跌) but above yesterday's open,
    so twstock labels it 量縮價不跌 and can emit a Buy.
    """
    start = datetime.date(2024, 1, 1)
    rows = _flat_prefix(MIN_SAMPLES_FOR_BFP - 2, start)
    yesterday = start + datetime.timedelta(days=MIN_SAMPLES_FOR_BFP - 2)
    today = yesterday + datetime.timedelta(days=1)
    rows.extend(
        [
            _bar(yesterday, open_=10.0, close=20.0, capacity=20_000),
            _bar(today, open_=18.0, close=15.0, capacity=9_000),
        ]
    )
    stock = _stock(rows)
    twstock = BestFourPoint(stock)
    grs = _GrsBestFourPoint(stock)

    # capacity today < yesterday, so both volume-shrink rules are in play.
    assert twstock.best_buy_2() is True  # 15 > yesterday's *open* 10
    assert grs.best_buy_2() is False  # 15 > yesterday's *close* 20? no
    assert twstock.best_sell_2() is False  # 15 < yesterday's *open* 10? no
    assert grs.best_sell_2() is True  # 15 < yesterday's *close* 20


def test_twstock_bias_tuple_stays_unconditionally_truthy_on_random_series():
    """Compressed form of the README experiment (20 000 sequences → 100% signal).

    500 draws is enough to notice if `bias_ratio()` stops being a non-empty
    tuple, which is the actual invariant. The 100%-signal figure is an
    observation about random walks, not a contract -- we only assert the gate.
    """
    rng = random.Random(0)
    start = datetime.date(2020, 1, 2)  # a Thursday; dates themselves do not matter

    for _ in range(500):
        price = 100.0
        rows: list[DailyPrice] = []
        for i in range(20):
            delta = rng.uniform(-4.0, 4.0)
            open_ = max(1.0, price + rng.uniform(-2.0, 2.0))
            close = max(1.0, price + delta)
            high = max(open_, close) + rng.uniform(0.0, 1.5)
            low = max(0.5, min(open_, close) - rng.uniform(0.0, 1.5))
            rows.append(
                _bar(
                    start + datetime.timedelta(days=i),
                    open_=open_,
                    close=close,
                    capacity=rng.randint(1_000, 50_000),
                    high=high,
                    low=low,
                )
            )
            price = close

        stock = _stock(rows)
        twstock = BestFourPoint(stock)
        grs = _GrsBestFourPoint(stock)

        raw = twstock.bias_ratio()
        assert isinstance(raw, tuple) and raw
        assert grs.bias_ratio() is raw[0]
        # plus-side too -- same function, opposite position flag
        raw_plus = twstock.bias_ratio(True)
        assert isinstance(raw_plus, tuple) and raw_plus
        assert grs.bias_ratio(True) is raw_plus[0]


def test_public_best_four_point_uses_grs_overrides_on_the_pullback_fixture():
    """Same bars as defect 2, through the function the API actually calls."""
    start = datetime.date(2024, 1, 1)
    rows = _flat_prefix(MIN_SAMPLES_FOR_BFP - 2, start)
    yesterday = start + datetime.timedelta(days=MIN_SAMPLES_FOR_BFP - 2)
    today = yesterday + datetime.timedelta(days=1)
    rows.extend(
        [
            _bar(yesterday, open_=10.0, close=20.0, capacity=20_000),
            _bar(today, open_=18.0, close=15.0, capacity=9_000),
        ]
    )
    stock = _stock(rows)

    twstock = best_four_point(stock, rule_set="twstock")
    grs = best_four_point(stock, rule_set="grs")

    # Goes through `_engine()` / `best_four_point()`, not just the overrides.
    # The two rule sets must be able to disagree on this fixture; a refactor
    # that drops `rule_set` would make them identical.
    assert twstock.reasons != grs.reasons or twstock.signal != grs.signal
