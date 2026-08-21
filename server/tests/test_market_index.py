"""Pins how TAIEX and the OTC index are registered and how their monthly
reports become `DATATUPLE` rows.

The live endpoints are not called: a TWSE-shaped payload and a TPEx-shaped
payload (copied from a real month, then trimmed) are enough to catch a merge
that forgets the 張→股 scale, a date parser that treats 2026/08/03 as ROC
year 3937, or a registry that drops `o00` again.
"""

from __future__ import annotations

from app.services import market_index
from app.services.market_index import IndexFetcher, _report_date, _table_rows


def test_o00_is_a_first_class_index():
    meta = market_index.get("o00")
    assert meta is not None
    assert market_index.is_index("o00")
    assert meta.channel == "otc_o00.tw"
    assert meta.data_source == "tpex"
    assert meta.volume_scale == 1000
    assert meta.month_param(2026, 8) == "2026/08/01"


def test_t00_month_param_stays_compact():
    meta = market_index.get("t00")
    assert meta is not None
    assert meta.month_param(2026, 8) == "20260801"
    assert meta.volume_scale == 1


def test_search_terms_split_the_two_indices():
    t00 = market_index.get("t00")
    o00 = market_index.get("o00")
    assert t00 is not None and o00 is not None
    assert market_index.matches(t00, "大盤")
    assert market_index.matches(t00, "TAIEX")
    assert not market_index.matches(t00, "櫃買")
    assert market_index.matches(o00, "櫃買")
    assert market_index.matches(o00, "o00")
    assert market_index.matches(o00, "tpex")
    assert not market_index.matches(o00, "加權")


def test_report_date_distinguishes_roc_from_gregorian():
    roc = _report_date("115/08/03")
    gregorian = _report_date("2026/08/03")
    assert roc is not None and gregorian is not None
    assert roc.date() == gregorian.date()
    assert roc.year == 2026


def test_table_rows_reads_both_envelopes():
    twse = {"stat": "OK", "data": [["115/08/03", "1", "2", "3", "4"]]}
    tpex = {
        "stat": "ok",
        "tables": [{"data": [["2026/08/03", "1", "2", "3", "4", "5"]]}],
    }
    assert _table_rows(twse)[0][0] == "115/08/03"
    assert _table_rows(tpex)[0][0] == "2026/08/03"
    assert _table_rows({"stat": "ok", "tables": []}) == []


def test_merge_twse_keeps_shares_and_nt_dollars():
    ohlc = [["115/08/03", "23,000.00", "23,100.00", "22,900.00", "23,050.00"]]
    volume = [["115/08/03", "8,000,000,000", "400,000,000,000", "3,000,000", "23050.00", "50.00"]]
    rows = IndexFetcher._merge(ohlc, volume, volume_scale=1)
    assert len(rows) == 1
    row = rows[0]
    assert row.date.year == 2026
    assert row.open == 23000.0
    assert row.close == 23050.0
    assert row.capacity == 8_000_000_000
    assert row.turnover == 400_000_000_000
    assert row.transaction == 3_000_000
    assert row.change == 50.0


def test_merge_tpex_scales_lots_and_joins_mixed_date_formats():
    """TPEx OHLC is Gregorian; the volume report is ROC. Volume is 張 / 仟元."""
    ohlc = [["2026/08/03", "349.29", "365.82", "349.14", "362.89", "15.04"]]
    volume = [["115/08/03", "682,447", "132,226,012", "654,724", 362.89, 15.04]]
    rows = IndexFetcher._merge(ohlc, volume, volume_scale=1000)
    assert len(rows) == 1
    row = rows[0]
    assert row.date.year == 2026 and row.date.month == 8 and row.date.day == 3
    assert row.open == 349.29
    assert row.high == 365.82
    assert row.low == 349.14
    assert row.close == 362.89
    assert row.capacity == 682_447_000
    assert row.turnover == 132_226_012_000
    assert row.transaction == 654_724
    assert row.change == 15.04


def test_merge_tpex_falls_back_to_ohlc_change_when_volume_is_missing():
    ohlc = [["2026/08/03", "349.29", "365.82", "349.14", "362.89", "15.04"]]
    rows = IndexFetcher._merge(ohlc, [], volume_scale=1000)
    assert rows[0].capacity is None
    assert rows[0].change == 15.04
