"""Pins the feature set the AI prompt is built from.

These numbers are the entire input to the model: if `close_vs_ma20_pct` silently
changes sign convention, or `consecutive_days` starts counting bars instead of
moves, every verdict shifts and nothing else in the system notices -- the model
will keep producing fluent, confident prose either way. That is the whole reason
this layer is pure and separately tested.
"""

from __future__ import annotations

import datetime

from app.models import DailyPrice
from app.services.analysis import features

SID = "2330"
START = datetime.date(2024, 1, 2)


def _bar(
    i: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    capacity: int = 10_000,
) -> DailyPrice:
    open_ = close if open_ is None else open_
    return DailyPrice(
        sid=SID,
        date=START + datetime.timedelta(days=i),
        open=open_,
        close=close,
        high=high if high is not None else max(open_, close),
        low=low if low is not None else min(open_, close),
        capacity=capacity,
    )


def _flat(n: int, price: float = 50.0, capacity: int = 10_000) -> list[DailyPrice]:
    return [_bar(i, close=price, capacity=capacity) for i in range(n)]


def test_too_few_bars_yields_no_features():
    """A verdict drawn from a handful of bars would be false precision."""
    assert features.extract(_flat(features.MIN_SAMPLES_FOR_AI - 1)) is None
    assert features.extract(_flat(features.MIN_SAMPLES_FOR_AI)) is not None


def test_extract_is_a_pure_function_of_the_rows():
    """No clock, no session -- the same bars must give the same features.

    This is what lets a backtest replay a past date by slicing the series.
    """
    rows = _flat(30)
    assert features.extract(rows) == features.extract(rows)


def test_consecutive_days_counts_moves_and_carries_the_sign():
    rows = _flat(25)
    for i, price in enumerate([51.0, 52.0, 53.0], start=25):
        rows.append(_bar(i, close=price))
    assert features.extract(rows).momentum.consecutive_days == 3

    rows = _flat(25)
    for i, price in enumerate([49.0, 48.0], start=25):
        rows.append(_bar(i, close=price))
    assert features.extract(rows).momentum.consecutive_days == -2

    # An unchanged close ends the run rather than extending it in either
    # direction: "連漲" stops being true the day the price stops rising.
    assert features.extract(_flat(25)).momentum.consecutive_days == 0


def test_ma_alignment_reads_the_short_to_long_ordering():
    rising = [_bar(i, close=40.0 + i) for i in range(30)]
    assert features.extract(rising).ma.alignment == "bullish"

    falling = [_bar(i, close=100.0 - i) for i in range(30)]
    assert features.extract(falling).ma.alignment == "bearish"

    assert features.extract(_flat(30)).ma.alignment == "mixed"


def test_close_above_its_average_is_a_positive_percentage():
    """Sign convention: positive means the close is *above* the average."""
    rising = [_bar(i, close=40.0 + i) for i in range(30)]
    ma = features.extract(rising).ma
    assert ma.close_vs_ma5_pct > 0
    assert ma.close_vs_ma20_pct > ma.close_vs_ma5_pct  # further above the slower one


def test_range_position_is_zero_at_the_low_and_a_hundred_at_the_high():
    at_high = _flat(29) + [_bar(29, close=60.0, high=60.0, low=50.0)]
    assert features.extract(at_high).range.position_pct == 100.0
    assert features.extract(at_high).range.drawdown_from_high_pct == 0.0

    at_low = _flat(29, price=60.0) + [_bar(29, close=50.0, high=60.0, low=50.0)]
    extracted = features.extract(at_low).range
    assert extracted.position_pct == 0.0
    assert extracted.drawdown_from_high_pct < 0  # below the window high


def test_volume_trend_has_a_dead_zone_around_the_average():
    """Without a band, every ordinary session reads as a volume event."""
    steady = _flat(30, capacity=10_000)
    assert features.extract(steady).volume.trend == "steady"

    spike = _flat(29, capacity=10_000) + [_bar(29, close=50.0, capacity=30_000)]
    assert features.extract(spike).volume.trend == "expanding"
    assert features.extract(spike).volume.ratio_to_avg5 > features.VOLUME_EXPANDING

    dry = _flat(29, capacity=10_000) + [_bar(29, close=50.0, capacity=2_000)]
    assert features.extract(dry).volume.trend == "contracting"


def test_gap_is_measured_against_the_previous_close_not_the_previous_open():
    """The same distinction the grs/twstock 量縮價不跌 defect turned on."""
    rows = _flat(28)
    rows.append(_bar(28, open_=40.0, close=50.0))  # long red: open 40, close 50
    rows.append(_bar(29, open_=45.0, close=45.0))  # opens below yesterday's close

    gap = features.extract(rows).momentum.gap_pct
    assert gap == -10.0  # 45 vs previous *close* 50, not previous open 40


def test_bias_series_is_the_one_the_rule_gate_uses():
    """Shared with 四大買賣點 so both verdicts answer the same question."""
    from app.services.analysis import traditional

    rows = [_bar(i, close=40.0 + (i % 7)) for i in range(40)]
    stock = traditional.build_stock(rows)

    extracted = features.extract(rows).bias_3_6
    expected = [round(b, 4) for b in stock.ma_bias_ratio(3, 6)[-5:]]
    assert extracted == expected


def test_as_of_is_the_last_usable_trading_day():
    """Days the exchange reported as '--' must not become the verdict's date."""
    rows = _flat(25)
    untraded = DailyPrice(
        sid=SID, date=START + datetime.timedelta(days=25),
        open=None, close=None, high=None, low=None, capacity=None,
    )
    rows.append(untraded)

    assert features.as_of_date(rows) == START + datetime.timedelta(days=24)
    assert features.extract(rows).as_of == START + datetime.timedelta(days=24)
