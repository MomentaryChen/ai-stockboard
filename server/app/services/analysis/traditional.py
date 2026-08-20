"""Traditional (rule-based) technical analysis.

Moving averages and the 四大買賣點 signal, both computed from the prices already
in our database. The maths comes from the twstock library's Analytics /
BestFourPoint implementation, reached through a thin adapter, so no extra trip
to the exchange is needed to score a stock.

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


def best_four_point(stock: _CachedStock) -> BestFourPointResult:
    if len(stock.price) < MIN_SAMPLES_FOR_BFP:
        return BestFourPointResult(
            signal="hold",
            label="資料不足",
            reasons=[f"需要至少 {MIN_SAMPLES_FOR_BFP} 個交易日才能判斷"],
        )

    result = BestFourPoint(stock).best_four_point()
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
