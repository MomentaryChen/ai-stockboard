"""Pins the chip parsers to the column layouts TWSE and TPEX actually publish.

A swapped 投信/自營 column would still produce tidy numbers and a plausible
連買 streak, which is why these fixtures are real rows (興富發 on 2026-08-20,
穩懋 the same day) rather than round numbers. If an upstream reshape moves a
net, these fail before the card starts lying.
"""

from __future__ import annotations

from app.services.chip import (
    parse_mi_margn,
    parse_t86,
    parse_tpex_inst,
    parse_tpex_margin,
    streak_and_net,
)

# 2542 興富發, T86 2026-08-20. Foreign ex-dealer 2,970,473 + dealer 0;
# trust +1,000; dealer -108,876; total 2,862,597.
T86_2542 = [
    "2542",
    "興富發",
    "5,359,473",
    "2,389,000",
    "2,970,473",
    "0",
    "0",
    "0",
    "18,000",
    "17,000",
    "1,000",
    "-108,876",
    "6,000",
    "34,000",
    "-28,000",
    "52,060",
    "132,936",
    "-80,876",
    "2,862,597",
]

MARGN_2542 = [
    "2542",
    "興富發",
    "230",
    "183",
    "3",
    "3,766",
    "3,810",
    "538,270",
    "32",
    "0",
    "0",
    "116",
    "84",
    "538,270",
    "0",
    "",
]

# 3105 穩懋, TPEX 2026-08-20. 外資合計 1,223,824; 投信 213,172;
# 自營合計 -235,134; 三大法人 1,201,862.
TPEX_INST_3105 = [
    "3105",
    "穩懋",
    "6,091,021",
    "4,867,197",
    "1,223,824",
    "0",
    "0",
    "0",
    "6,091,021",
    "4,867,197",
    "1,223,824",
    "213,172",
    "0",
    "213,172",
    "346,167",
    "568,005",
    "-221,838",
    "57,154",
    "70,450",
    "-13,296",
    "403,321",
    "638,455",
    "-235,134",
    "1,201,862",
]

TPEX_MARGN_3105 = [
    "3105",
    "穩懋",
    "45,284",
    "515",
    "795",
    "2",
    "45,002",
    "552",
    "42.46",
    "105,985",
    "970",
    "74",
    "118",
    "0",
    "926",
    "46",
    "0.87",
    "105,985",
    "30",
    "",
]


def test_t86_adds_foreign_dealer_into_foreign_net():
    rows = parse_t86({"stat": "OK", "data": [T86_2542]})
    assert len(rows) == 1
    row = rows[0]
    assert row.sid == "2542"
    assert row.foreign_net == 2_970_473
    assert row.trust_net == 1_000
    assert row.dealer_net == -108_876
    assert row.total_net == 2_862_597
    assert row.foreign_net + row.trust_net + row.dealer_net == row.total_net


def test_t86_skips_a_short_row_instead_of_guessing_columns():
    short = T86_2542[:10]
    assert parse_t86({"stat": "OK", "data": [short]}) == []


def test_t86_rejects_a_failed_stat():
    assert parse_t86({"stat": "很抱歉，沒有符合條件的資料!"}) == []


def test_mi_margn_reads_the_stock_table_not_the_summary():
    payload = {
        "stat": "OK",
        "tables": [
            {"title": "summary", "fields": [], "data": []},
            {
                "title": "融資融券彙總 (股票)",
                "fields": ["代號", "名稱"],
                "data": [MARGN_2542],
            },
        ],
    }
    rows = parse_mi_margn(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.sid == "2542"
    assert row.margin_balance == 3810
    assert row.margin_change == 44
    assert row.short_balance == 84
    assert row.short_change == -32


def test_tpex_inst_uses_the_合計_columns():
    payload = {"tables": [{"fields": ["代號"], "data": [TPEX_INST_3105]}]}
    rows = parse_tpex_inst(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.sid == "3105"
    assert row.foreign_net == 1_223_824
    assert row.trust_net == 213_172
    assert row.dealer_net == -235_134
    assert row.total_net == 1_201_862
    assert row.foreign_net + row.trust_net + row.dealer_net == row.total_net


def test_tpex_margin_change_is_today_minus_prev():
    payload = {"tables": [{"data": [TPEX_MARGN_3105]}]}
    rows = parse_tpex_margin(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.sid == "3105"
    assert row.margin_balance == 45_002
    assert row.margin_change == 45_002 - 45_284
    assert row.short_balance == 926
    assert row.short_change == 926 - 970


def test_streak_counts_same_sign_from_the_newest_day():
    flow = streak_and_net([2_970_473, 1_000_000, 500_000, -10, 20])
    assert flow.streak == "buy"
    assert flow.streak_days == 3
    assert flow.net_5d == 2_970_473 + 1_000_000 + 500_000 - 10 + 20


def test_streak_skips_leading_holes_then_breaks_on_zero():
    flow = streak_and_net([None, 100, 50, 0, 20])
    assert flow.streak == "buy"
    assert flow.streak_days == 2


def test_streak_none_when_every_day_is_flat_or_missing():
    flow = streak_and_net([0, None, 0])
    assert flow.streak == "none"
    assert flow.streak_days == 0
    assert flow.net_5d == 0
