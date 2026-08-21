"""Derived facts about a price series, computed once and handed to the AI engine.

This module exists to keep the language model away from arithmetic. A prompt
carrying 60 rows of OHLCV makes every answer depend on the model adding numbers
up correctly, which is the thing it is least reliable at; a prompt carrying
"close is 3.2% above MA20, volume is 1.8x its 5-day average, price sits at 74%
of its 60-day range" asks it to do only the part it is good at.

Everything here is a pure function of the stored bars: no database session, no
network, no clock. That is what makes the feature set testable, and it is also
what will let a backtest replay any past date by slicing the same rows -- the
features for 2024-03-14 are whatever `extract()` returns for the bars up to
2024-03-14, with nothing else to reproduce.
"""

from __future__ import annotations

import datetime
import statistics

from app.models import DailyPrice
from app.schemas import (
    MaFeatures,
    MomentumFeatures,
    PriceFeatures,
    RangeFeatures,
    VolatilityFeatures,
    VolumeFeatures,
)
from app.services.analysis import traditional

#: Below this there is not enough series for the 20-day statistics to mean
#: anything, and a verdict drawn from four bars would be false precision.
MIN_SAMPLES_FOR_AI = 20

#: 位階 and drawdown are read over a quarter's trading, or the whole sample when
#: it is shorter.
RANGE_WINDOW = 60

#: Volume and volatility windows. Kept separate from RANGE_WINDOW because they
#: answer "what is normal lately", not "where are we in the cycle".
SHORT_WINDOW = 5
LONG_WINDOW = 20

#: Volume is called expanding/contracting only outside this band around its own
#: average. Without a dead zone every ordinary session reads as a volume event.
VOLUME_EXPANDING = 1.2
VOLUME_CONTRACTING = 0.8


def _pct(numerator: float, denominator: float) -> float | None:
    """Percentage change, or None when the base is zero or missing."""
    if not denominator:
        return None
    return round((numerator / denominator - 1) * 100, 2)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _ma(prices: list[float], window: int) -> float | None:
    if len(prices) < window:
        return None
    return round(statistics.fmean(prices[-window:]), 2)


def _alignment(
    ma5: float | None, ma10: float | None, ma20: float | None
) -> str:
    if ma5 is None or ma10 is None or ma20 is None:
        return "unknown"
    if ma5 > ma10 > ma20:
        return "bullish"
    if ma5 < ma10 < ma20:
        return "bearish"
    return "mixed"


def _consecutive_days(prices: list[float]) -> int:
    """Signed run length of same-direction closes ending at the last bar.

    +3 means three consecutive up days, -2 two down days, 0 an unchanged close.
    A run is one of the few pieces of path information a set of averages throws
    away, and it is exactly what "已經連漲五天" arguments are made of.
    """
    if len(prices) < 2:
        return 0

    direction = 0
    if prices[-1] > prices[-2]:
        direction = 1
    elif prices[-1] < prices[-2]:
        direction = -1
    if direction == 0:
        return 0

    run = 0
    for i in range(len(prices) - 1, 0, -1):
        step = 1 if prices[i] > prices[i - 1] else (-1 if prices[i] < prices[i - 1] else 0)
        if step != direction:
            break
        run += 1
    return run * direction


def _ma_features(prices: list[float]) -> MaFeatures:
    close = prices[-1]
    ma5 = _ma(prices, 5)
    ma10 = _ma(prices, 10)
    ma20 = _ma(prices, 20)
    ma60 = _ma(prices, 60)
    return MaFeatures(
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        close_vs_ma5_pct=_pct(close, ma5) if ma5 else None,
        close_vs_ma20_pct=_pct(close, ma20) if ma20 else None,
        close_vs_ma60_pct=_pct(close, ma60) if ma60 else None,
        alignment=_alignment(ma5, ma10, ma20),
    )


def _volume_features(capacities: list[int]) -> VolumeFeatures:
    latest = capacities[-1] if capacities else None
    avg5 = _mean([float(c) for c in capacities[-SHORT_WINDOW:]])
    avg20 = _mean([float(c) for c in capacities[-LONG_WINDOW:]])

    ratio5 = round(latest / avg5, 2) if latest and avg5 else None
    ratio20 = round(latest / avg20, 2) if latest and avg20 else None

    trend = "unknown"
    if ratio5 is not None:
        if ratio5 >= VOLUME_EXPANDING:
            trend = "expanding"
        elif ratio5 <= VOLUME_CONTRACTING:
            trend = "contracting"
        else:
            trend = "steady"

    return VolumeFeatures(
        latest_shares=latest,
        avg5_shares=int(avg5) if avg5 else None,
        avg20_shares=int(avg20) if avg20 else None,
        ratio_to_avg5=ratio5,
        ratio_to_avg20=ratio20,
        trend=trend,
    )


def _momentum_features(prices: list[float], opens: list[float]) -> MomentumFeatures:
    close = prices[-1]

    def back(n: int) -> float | None:
        return _pct(close, prices[-1 - n]) if len(prices) > n else None

    gap = None
    if len(prices) >= 2 and len(opens) >= 1:
        gap = _pct(opens[-1], prices[-2])

    return MomentumFeatures(
        return_1d_pct=back(1),
        return_5d_pct=back(5),
        return_20d_pct=back(20),
        return_60d_pct=back(60),
        consecutive_days=_consecutive_days(prices),
        gap_pct=gap,
    )


def _range_features(
    prices: list[float], highs: list[float], lows: list[float]
) -> RangeFeatures:
    window = min(RANGE_WINDOW, len(prices))
    high = max(highs[-window:]) if highs else None
    low = min(lows[-window:]) if lows else None
    close = prices[-1]

    position = None
    if high is not None and low is not None and high > low:
        position = round((close - low) / (high - low) * 100, 2)

    drawdown = None
    if high:
        drawdown = round((close - high) / high * 100, 2)

    return RangeFeatures(
        window_days=window,
        high=round(high, 2) if high is not None else None,
        low=round(low, 2) if low is not None else None,
        position_pct=position,
        drawdown_from_high_pct=drawdown,
    )


def _volatility_features(prices: list[float]) -> VolatilityFeatures:
    window = prices[-(LONG_WINDOW + 1) :]
    returns = [
        (window[i] / window[i - 1] - 1) * 100
        for i in range(1, len(window))
        if window[i - 1]
    ]
    if len(returns) < 2:
        return VolatilityFeatures(stdev_20d_pct=None, avg_abs_move_20d_pct=None)

    return VolatilityFeatures(
        stdev_20d_pct=round(statistics.stdev(returns), 2),
        avg_abs_move_20d_pct=round(statistics.fmean([abs(r) for r in returns]), 2),
    )


def extract(rows: list[DailyPrice]) -> PriceFeatures | None:
    """Feature set for the bars given, or None when there are too few.

    Callers pass the rows they want the verdict to be *as of*; nothing here
    reads a clock, so slicing the series to a past date is all a replay needs.
    """
    stock = traditional.build_stock(rows)
    prices = stock.price
    if len(prices) < MIN_SAMPLES_FOR_AI:
        return None

    # The same 3/6 bias series the 四大買賣點 gate pivots on. Sharing it means the
    # AI is asked about the number the rule engine actually used, so the two
    # verdicts on the card are answers to the same question.
    bias = [round(b, 4) for b in stock.ma_bias_ratio(3, 6)[-5:]]

    return PriceFeatures(
        as_of=stock.date[-1],
        sample_size=len(prices),
        latest_close=round(prices[-1], 2),
        ma=_ma_features(prices),
        volume=_volume_features(stock.capacity),
        momentum=_momentum_features(prices, stock.open),
        range=_range_features(prices, stock.high, stock.low),
        volatility=_volatility_features(prices),
        bias_3_6=bias,
    )


def as_of_date(rows: list[DailyPrice]) -> datetime.date | None:
    """Trading day the usable bars end on -- the cache key for a verdict."""
    usable = traditional.usable_rows(rows)
    return usable[-1].date if usable else None
