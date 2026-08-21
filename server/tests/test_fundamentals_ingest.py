"""Pins the parsing that turns three upstream reports into one annual row.

Nothing here touches the network. What it does pin is every place the sources
disagree with each other, because those are the failures that produce numbers
rather than errors -- a wrong EPS looks exactly like a right one until someone
checks it against a filing:

  * the exchanges publish EPS **cumulative year-to-date**, FinMind publishes it
    **per quarter**. Read either as the other and a company's earnings are out
    by a factor of roughly four, in opposite directions;
  * `稅後淨利` arrives in thousands from the exchange and in units from
    FinMind, so ROE would be out by 1000x depending on which ingest wrote the
    row;
  * `EquityAttributableToOwnersOfParent` means net income in one FinMind
    dataset and total equity in the other;
  * TPEx publishes `DividendPerShare`, whose name contains "per", next to
    `PriceEarningRatio` -- so a loose field match stores a 0.5 "PE" that
    passes every cheapness threshold there is.
"""

from __future__ import annotations

import datetime

from app.services import finmind, fundamentals, valuation

# --- valuation: the exchange's own PE / PBR / yield ---------------------------

TODAY = datetime.date(2026, 8, 20)


def test_twse_valuation_rows_parse_with_their_roc_date():
    payload = [
        {
            "Date": "1150820",
            "Code": "2330",
            "Name": "台積電",
            "PEratio": "21.35",
            "DividendYield": "1.42",
            "PBratio": "6.08",
        }
    ]
    (row,) = valuation.parse_twse(payload, TODAY)

    assert row.sid == "2330"
    assert row.date == datetime.date(2026, 8, 20)  # 民國 115 -> 2026
    assert row.pe_ratio == 21.35
    assert row.pb_ratio == 6.08
    assert row.dividend_yield == 1.42
    assert row.source == "twse"


def test_a_blank_pe_stays_null_rather_than_becoming_zero():
    """TWSE leaves PE empty for a company with no positive trailing earnings.

    Zero would claim it trades at zero times earnings, which is the cheapest
    possible stock rather than an undefined ratio.
    """
    payload = [
        {"Date": "1150820", "Code": "1101", "PEratio": "", "DividendYield": "3.23",
         "PBratio": "0.80"}
    ]
    (row,) = valuation.parse_twse(payload, TODAY)

    assert row.pe_ratio is None
    assert row.pb_ratio == 0.80


def test_a_zero_filler_is_treated_as_undefined_too():
    """Both exchanges use 0 as a filler often enough to matter.

    Left as 0 it would put every loss-making company at the top of a
    cheapness screen.
    """
    payload = [{"Date": "1150820", "Code": "9999", "PEratio": "0.00", "PBratio": "0"}]
    (row,) = valuation.parse_twse(payload, TODAY)

    assert row.pe_ratio is None
    assert row.pb_ratio is None


def test_tpex_pe_is_read_from_the_right_field_not_dividend_per_share():
    """The trap: "DividendPerShare".lower() contains "per".

    A loose match would store 0.5 as the PE ratio, and 0.5 clears every
    cheapness ceiling in chen_rules by a factor of twenty.
    """
    payload = [
        {
            "Date": "1150820",
            "SecuritiesCompanyCode": "1240",
            "CompanyName": "茂生農經",
            "PriceEarningRatio": "10.61",
            "DividendPerShare": "0.50000000",
            "YieldRatio": "0.88",
            "PriceBookRatio": "1.68",
        }
    ]
    (row,) = valuation.parse_tpex(payload, TODAY)

    assert row.sid == "1240"
    assert row.pe_ratio == 10.61
    assert row.pb_ratio == 1.68
    assert row.dividend_yield == 0.88


def test_a_row_with_no_instrument_is_dropped():
    assert valuation.parse_tpex([{"PriceEarningRatio": "8"}], TODAY) == []
    assert valuation.parse_twse([{"PEratio": "8"}], TODAY) == []


# --- exchange EPS: cumulative, and only Q4 is annual --------------------------


def _twse_eps(quarter: str, eps: str, net_income: str = "4569799.00") -> dict:
    return {
        "年度": "115",
        "季別": quarter,
        "公司代號": "1101",
        "基本每股盈餘(元)": eps,
        "稅後淨利": net_income,
    }


def test_only_the_fourth_quarter_is_stored_as_the_annual_figure():
    """`t187ap14` is cumulative year-to-date, so Q1-Q3 are part-years.

    Verified upstream: 台泥 115Q2 reads 0.38, which is Q1+Q2 (0.10+0.29), not
    Q2 alone. Storing a Q2 row as the year would understate earnings enough to
    fail the Earn rule for the entire board.
    """
    payload = [_twse_eps(q, e) for q, e in (("1", "0.10"), ("2", "0.38"), ("3", "0.60"))]
    assert fundamentals.parse_exchange_eps(payload, fundamentals._TWSE_EPS_FIELDS, "twse_eps") == []

    payload.append(_twse_eps("4", "0.95"))
    (row,) = fundamentals.parse_exchange_eps(
        payload, fundamentals._TWSE_EPS_FIELDS, "twse_eps"
    )
    assert row.eps == 0.95


def test_the_roc_year_becomes_a_gregorian_one():
    (row,) = fundamentals.parse_exchange_eps(
        [_twse_eps("4", "0.95")], fundamentals._TWSE_EPS_FIELDS, "twse_eps"
    )
    assert row.year == 2026


def test_net_income_is_normalised_out_of_thousands():
    """The exchange reports 稅後淨利 in thousands, FinMind in units.

    Left unnormalised, an ROE audit would be out by 1000x depending on which
    ingest happened to write the row.
    """
    (row,) = fundamentals.parse_exchange_eps(
        [_twse_eps("4", "0.95", net_income="4569799.00")],
        fundamentals._TWSE_EPS_FIELDS,
        "twse_eps",
    )
    assert row.net_income == 4_569_799_000.0


def test_the_exchange_report_never_invents_an_roe():
    """It carries no equity, so there is no denominator to divide by."""
    (row,) = fundamentals.parse_exchange_eps(
        [_twse_eps("4", "0.95")], fundamentals._TWSE_EPS_FIELDS, "twse_eps"
    )
    assert row.roe is None
    assert row.equity is None


def test_tpex_uses_its_own_field_names_for_the_same_report():
    payload = [
        {
            "Year": "115",
            "季別": "4",
            "SecuritiesCompanyCode": "1240",
            "基本每股盈餘": "2.85",
            "稅後淨利": "126506.00",
        }
    ]
    (row,) = fundamentals.parse_exchange_eps(
        payload, fundamentals._TPEX_EPS_FIELDS, "tpex_eps"
    )
    assert row.sid == "1240"
    assert row.eps == 2.85
    assert row.year == 2026


# --- FinMind: per-quarter, and two datasets that share a key name -------------


def _fm_rows(kind: str, year: int, values: dict[str, float]) -> list[dict]:
    return [
        {"date": f"{year}-{month}-30", "stock_id": "2330", "type": kind, "value": v}
        for month, v in values.items()
    ]


def test_finmind_quarters_are_summed_into_the_published_annual_figure():
    """TSMC 2015: 3.05+3.06+2.91+2.81 = 11.83 against a reported 11.82.

    The residual is rounding of the quarterly values, not a parsing error --
    which is exactly why this is asserted as a sum rather than against the
    filing.
    """
    rows = _fm_rows("EPS", 2015, {"03": 3.05, "06": 3.06, "09": 2.91, "12": 2.81})
    assert finmind._annual_sums(rows, "EPS") == {2015: 11.83}


def test_a_part_year_is_not_reported_as_an_annual_figure():
    """Three quarters of a profitable company is a smaller number, not a
    smaller company -- and the Earn rule cannot tell those apart."""
    rows = _fm_rows("EPS", 2026, {"03": 8.7, "06": 9.56, "09": 12.55})
    assert finmind._annual_sums(rows, "EPS") == {}


def test_equity_is_observed_at_year_end_rather_than_summed():
    """It is a balance, not a flow. Summing four quarters would quadruple it."""
    rows = _fm_rows(
        finmind.EQUITY_TOTAL_TYPE, 2024, {"03": 100.0, "06": 110.0, "09": 120.0, "12": 130.0}
    )
    assert finmind._year_end(rows, finmind.EQUITY_TOTAL_TYPE) == {2024: 130.0}


def test_roe_uses_average_equity_when_the_prior_year_is_known():
    """A company that raised capital in December would otherwise show a year's
    profit measured against equity it held for a fortnight."""
    # 100 profit against equity that grew 800 -> 1200 across the year.
    assert finmind._roe_percent(100.0, 1200.0, 800.0) == 10.0
    # Without the opening balance, year-end is the honest fallback.
    assert finmind._roe_percent(100.0, 1200.0, None) == round(100 / 1200 * 100, 4)


def test_roe_is_undefined_rather_than_zero_without_a_denominator():
    assert finmind._roe_percent(100.0, None, None) is None
    assert finmind._roe_percent(100.0, 0.0, 0.0) is None
    assert finmind._roe_percent(None, 1200.0, 800.0) is None


def _quarters(kind: str, year: int, value: float) -> list[dict]:
    return [
        {"date": f"{year}-{m}-30", "type": kind, "value": value}
        for m in ("03", "06", "09", "12")
    ]


def test_general_industry_is_measured_on_the_parent_basis():
    """Both bases are published, and parent-with-parent is the right pair."""
    statements = (
        _quarters(finmind.INCOME_PARENT_TYPE, 2024, 25.0)
        + _quarters("IncomeAfterTaxes", 2024, 30.0)
    )
    balance = [
        {"date": "2024-12-31", "type": finmind.EQUITY_PARENT_TYPE, "value": 1000.0},
        {"date": "2024-12-31", "type": finmind.EQUITY_TOTAL_TYPE, "value": 1200.0},
    ]
    income, equity = finmind._matched_pair(statements, balance)

    assert income[2024] == 100.0   # the parent numerator, not 120
    assert equity[2024] == 1000.0  # ...paired with the parent denominator


def test_a_financial_holding_falls_back_to_the_total_basis_on_both_sides():
    """華南金 publishes parent *income* but only 權益總計 for equity.

    Taking the parent numerator with the total denominator is the tempting
    bug: it silently understates ROE by whatever the minority interest is,
    and produces a number that looks entirely reasonable.
    """
    statements = (
        _quarters(finmind.INCOME_PARENT_TYPE, 2024, 25.0)
        # Financials use IncomeAfterTax, not IncomeAfterTaxes.
        + _quarters("IncomeAfterTax", 2024, 30.0)
    )
    balance = [{"date": "2024-12-31", "type": finmind.EQUITY_TOTAL_TYPE, "value": 1200.0}]
    income, equity = finmind._matched_pair(statements, balance)

    assert income[2024] == 120.0   # total with total...
    assert equity[2024] == 1200.0  # ...never one of each


def test_no_equity_at_all_yields_no_roe_rather_than_a_guess():
    statements = _quarters(finmind.INCOME_PARENT_TYPE, 2024, 25.0)
    income, equity = finmind._matched_pair(statements, [])
    assert equity == {}
    assert finmind._roe_percent(income.get(2024), equity.get(2024), None) is None
