"""Traditional (rule-based) technical analysis.

Moving averages and the 四大買賣點 signal, both computed from the prices already
in our database. The maths comes from the twstock library's Analytics /
BestFourPoint implementation, reached through a thin adapter, so no extra trip
to the exchange is needed to score a stock.

Two rule sets are available, selected per request:

  "grs"      the reference behaviour -- twstock's rules with the two porting
             defects described on `_GrsBestFourPoint` corrected. The default.
  "twstock"  twstock 1.5.1 exactly as published, kept so the two can be put
             side by side.

This is the deterministic half of the product; AI-assisted analysis lives in a
sibling module under the same package.
"""

import datetime

from twstock.analytics import Analytics, BestFourPoint

from app.models import DailyPrice
from app.schemas import (
    BestFourPointResult,
    MaSeriesPoint,
    MovingAverages,
)

MA_WINDOWS = (5, 10, 20, 60)
MIN_SAMPLES_FOR_BFP = 12

#: Selectable rule sets for 四大買賣點. See the module docstring.
RULE_SETS = ("grs", "twstock")
DEFAULT_RULE_SET = "grs"


class _CachedStock(Analytics):
    """Adapts our stored price rows to the interface twstock's analytics expect.

    BestFourPoint only reaches for .capacity / .price / .open plus the methods
    Analytics already provides, so this is all it takes -- and it keeps the
    library from doing its own network fetch.
    """

    def __init__(self, rows: list[DailyPrice]):
        self._rows = rows

    @property
    def date(self) -> list[datetime.date]:
        return [r.date for r in self._rows]

    @property
    def capacity(self) -> list[int]:
        return [int(r.capacity or 0) for r in self._rows]

    @property
    def price(self) -> list[float]:
        return [float(r.close) for r in self._rows]

    @property
    def close(self) -> list[float]:
        return self.price

    @property
    def open(self) -> list[float]:
        return [float(r.open) for r in self._rows]

    @property
    def high(self) -> list[float]:
        return [float(r.high) for r in self._rows]

    @property
    def low(self) -> list[float]:
        return [float(r.low) for r in self._rows]


def usable_rows(rows: list[DailyPrice]) -> list[DailyPrice]:
    """Drop days with no trade (TWSE reports '--' for open/close on those)."""
    return [
        r
        for r in rows
        if r.close is not None and r.open is not None and r.capacity is not None
    ]


def latest_moving_averages(stock: _CachedStock) -> MovingAverages:
    prices = stock.price
    values = {}
    for window in MA_WINDOWS:
        values[f"ma{window}"] = (
            stock.moving_average(prices, window)[-1] if len(prices) >= window else None
        )
    return MovingAverages(**values)


def ma_series(stock: _CachedStock) -> list[MaSeriesPoint]:
    """Per-day MA values, aligned to the trading dates, for charting."""
    prices = stock.price
    dates = stock.date
    n = len(prices)

    computed: dict[int, list[float]] = {}
    for window in MA_WINDOWS:
        computed[window] = stock.moving_average(prices, window) if n >= window else []

    points = []
    for i in range(n):
        values = {}
        for window in MA_WINDOWS:
            series = computed[window]
            idx = i - (window - 1)
            values[f"ma{window}"] = series[idx] if series and idx >= 0 else None
        points.append(MaSeriesPoint(date=dates[i], **values))
    return points


class _GrsBestFourPoint(BestFourPoint):
    """twstock's 四大買賣點, corrected back to the implementation it came from.

    twstock ported BestFourPoint from toomore/grs (`grs/best_buy_or_sell.py`,
    MIT, Toomore Chiang) and two things were lost on the way. Both are repaired
    here rather than in `vendor/`, which is deliberately kept byte-identical to
    PyPI twstock 1.5.1:

    1. **The 乖離 pre-condition never fires.** grs ends its `bias_ratio` call
       with `[0]` to pull the boolean out of the `(bool, index, value)` pivot
       tuple. twstock returns the whole tuple, and a non-empty tuple is always
       truthy -- so `if self.mins_bias_ratio() and any(check)` degenerates into
       `any(check)` and the gate rejects nothing.

    2. **「量縮價不跌」/「量縮價跌」 compare the wrong column.** grs measures
       today's close against *yesterday's close* (`price[-1] > price[-2]`);
       twstock measures it against yesterday's *open*. When the previous
       session closed well above its open, a day that actually fell gets
       labelled 價不跌 and can produce a Buy.

    The pivot maths itself was ported faithfully -- `ma_bias_ratio_pivot` is
    line-for-line equivalent to grs's `__cal_ma_bias_ratio_point` -- so nothing
    else needs overriding.

    One difference is left in place: grs rounds moving averages to 6 decimals,
    twstock to 2. It can only matter on near-ties, and closing it would mean
    forking `Analytics` as well.
    """

    def bias_ratio(self, position: bool = False) -> bool:
        return super().bias_ratio(position)[0]

    def best_buy_2(self) -> bool:
        return (
            self.stock.capacity[-1] < self.stock.capacity[-2]
            and self.stock.price[-1] > self.stock.price[-2]
        )

    def best_sell_2(self) -> bool:
        return (
            self.stock.capacity[-1] < self.stock.capacity[-2]
            and self.stock.price[-1] < self.stock.price[-2]
        )


def _engine(stock: _CachedStock, rule_set: str) -> BestFourPoint:
    return BestFourPoint(stock) if rule_set == "twstock" else _GrsBestFourPoint(stock)


def best_four_point(
    stock: _CachedStock, rule_set: str = DEFAULT_RULE_SET
) -> BestFourPointResult:
    if len(stock.price) < MIN_SAMPLES_FOR_BFP:
        return BestFourPointResult(
            signal="hold",
            label="資料不足",
            reasons=[f"需要至少 {MIN_SAMPLES_FOR_BFP} 個交易日才能判斷"],
        )

    result = _engine(stock, rule_set).best_four_point()
    if result is None:
        return BestFourPointResult(signal="hold", label="Don't touch", reasons=[])

    is_buy, why = result
    reasons = [w.strip() for w in why.split(",") if w.strip()]
    return BestFourPointResult(
        signal="buy" if is_buy else "sell",
        label="Buy" if is_buy else "Sell",
        reasons=reasons,
    )


def build_stock(rows: list[DailyPrice]) -> _CachedStock:
    return _CachedStock(usable_rows(rows))
