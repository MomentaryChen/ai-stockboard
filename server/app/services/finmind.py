"""FinMind client: annual EPS, net income and equity for one company.

The only third-party data source in this service, and it earns the exception
by being a **one-off data mover rather than a runtime dependency**. Every other
upstream here is an exchange endpoint the API calls to answer a request. This
one is called by a backfill job, writes into `fundamentals_annual`, and is
never touched again -- so if FinMind changes its terms or disappears, the rows
it delivered stay and nothing user-facing breaks.

The reason it is needed at all: the exchanges publish `t187ap14` as a *current
quarter* snapshot with no history parameter. Going forward that is enough --
one annual figure a year, free, no key. Going backward it gives nothing, and
Chen's 年年賺錢 is a question about the last decade.

Two conventions matter and they disagree with the exchange's:

* **FinMind EPS is per-quarter**; the exchanges publish cumulative
  year-to-date. Summing four FinMind quarters reproduces the published annual
  figure (TSMC 2015: 3.05+3.06+2.91+2.81 = 11.83 against a reported 11.82,
  the difference being rounding of the quarterly values). Reading it as
  cumulative would take Q4 alone and understate a year by roughly three
  quarters.
* **`EquityAttributableToOwnersOfParent` means two different things** depending
  on the dataset: net income attributable to the parent in the income
  statement, total equity attributable to the parent in the balance sheet.
  Pairing them is what makes a real ROE; confusing them makes a ratio of one
  number to itself.
"""

from __future__ import annotations

import collections
import datetime
import logging

from twstock.proxy import get_proxies, get_session

from app.config import get_settings
from app.throttle import SlidingWindowThrottle

logger = logging.getLogger(__name__)
settings = get_settings()

API_URL = "https://api.finmindtrade.com/api/v4/data"

STATEMENTS_DATASET = "TaiwanStockFinancialStatements"
BALANCE_DATASET = "TaiwanStockBalanceSheet"

EPS_TYPE = "EPS"

#: ROE is computed from a *matched* pair, and this is the whole subtlety of
#: this module.
#:
#: Consolidated profit includes minority interests; equity attributable to the
#: parent excludes them. Divide one by the other and the ratio is wrong for
#: every group that has minorities -- inflated or deflated depending on which
#: way round they were mixed. So the numerator and denominator are chosen
#: together: parent-with-parent, or total-with-total, never one of each.
#:
#: The pairing has to be dynamic because the datasets are not uniform by
#: industry. A general-industry company (2330) publishes both parent equity
#: and total equity; a financial holding (2880) publishes only 權益總計. Even
#: the total-income key differs -- `IncomeAfterTaxes` for general industry,
#: `IncomeAfterTax` for financials.
#:
#: `EquityAttributableToOwnersOfParent` appears in both datasets meaning two
#: different things: net income attributable to the parent in the income
#: statement, total equity attributable to the parent in the balance sheet.
INCOME_PARENT_TYPE = "EquityAttributableToOwnersOfParent"
EQUITY_PARENT_TYPE = "EquityAttributableToOwnersOfParent"

#: Fallbacks, in preference order, for the total-basis pair.
INCOME_TOTAL_TYPES = (
    "IncomeAfterTaxes",
    "IncomeAfterTax",
    "NetIncome",
    "TotalConsolidatedProfitForThePeriod",
)
EQUITY_TOTAL_TYPE = "Equity"

MAX_ATTEMPTS = 2
TIMEOUT = 30

#: Its own window, deliberately well under the published free-tier ceiling
#: (300/hour anonymous, 600/hour with a verified account). The backfill is not
#: urgent -- it is filling in a decade nobody has been waiting for -- so there
#: is no reason to run near a limit whose breach costs the whole run.
finmind_throttle = SlidingWindowThrottle(
    max_calls=settings.finmind_throttle_max_calls,
    window_seconds=settings.finmind_throttle_window_seconds,
)


class FinMindFailed(RuntimeError):
    """Reached and could not answer, or answered something unusable."""


def _get(dataset: str, sid: str, start: datetime.date) -> list[dict]:
    """One dataset for one company. Blocks on the throttle."""
    params = {
        "dataset": dataset,
        "data_id": sid,
        "start_date": start.isoformat(),
    }
    # Optional: the datasets this needs are served without one, but a verified
    # account doubles the hourly allowance, so a deployment that has a token
    # gets a faster backfill for free.
    if settings.finmind_token:
        params["token"] = settings.finmind_token

    session = get_session()
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        finmind_throttle.acquire()
        try:
            response = session.get(
                API_URL, params=params, proxies=get_proxies(), timeout=TIMEOUT
            )
            body = response.json()
        except Exception as exc:
            last = exc
            logger.warning(
                "finmind fetch failed dataset=%s sid=%s attempt=%d",
                dataset, sid, attempt + 1, exc_info=True,
            )
            continue

        if not isinstance(body, dict):
            last = FinMindFailed(f"unexpected body type {type(body)!r}")
            continue
        if body.get("status") != 200:
            # A quota refusal is not a transient error and must stop the run
            # rather than burn the remaining allowance retrying.
            raise FinMindFailed(
                f"{dataset} for {sid}: status={body.get('status')} msg={body.get('msg')}"
            )
        return body.get("data") or []

    raise FinMindFailed(f"{dataset} for {sid}: {last}")


def _annual_sums(rows: list[dict], wanted: str) -> dict[int, float]:
    """Sum one per-quarter series into calendar years.

    Only years with all four quarters are returned. A part-year would look
    exactly like a bad year to the Earn rule -- three quarters of a profitable
    company is a smaller number, not a smaller company -- and reporting it as
    the annual figure is how a checklist ends up failing its best names.
    """
    quarters: dict[int, dict[str, float]] = collections.defaultdict(dict)
    for row in rows:
        if row.get("type") != wanted:
            continue
        date = str(row.get("date") or "")
        value = row.get("value")
        if len(date) < 7 or value is None:
            continue
        try:
            quarters[int(date[:4])][date[5:7]] = float(value)
        except (ValueError, TypeError):
            continue

    return {
        year: round(sum(byq.values()), 4)
        for year, byq in quarters.items()
        if len(byq) == 4
    }


def _year_end(rows: list[dict], wanted: str) -> dict[int, float]:
    """The Q4 reading of a point-in-time series, per calendar year.

    Equity is a balance, not a flow: it is not summed, it is observed. The
    year-end observation is the one that pairs with a full year of income.
    """
    out: dict[int, float] = {}
    for row in rows:
        if row.get("type") != wanted:
            continue
        date = str(row.get("date") or "")
        value = row.get("value")
        if len(date) < 7 or value is None or date[5:7] != "12":
            continue
        try:
            out[int(date[:4])] = float(value)
        except (ValueError, TypeError):
            continue
    return out


def _roe_percent(
    income: float | None, equity_end: float | None, equity_open: float | None
) -> float | None:
    """Return on equity, in percent, against average equity where possible.

    Average of opening and closing equity rather than the year-end figure
    alone: a company that raised capital in December would otherwise show a
    year of profit measured against equity it held for a fortnight, which
    understates the return it actually earned. Falls back to year-end when
    the prior year is not stored -- which is the first year of any backfill.
    """
    if income is None or not equity_end:
        return None
    base = (equity_end + equity_open) / 2 if equity_open else equity_end
    if not base:
        return None
    return round(income / base * 100, 4)


def _first_series(
    rows: list[dict], candidates: tuple[str, ...], reducer
) -> dict[int, float]:
    """The first candidate type that yields anything, reduced to years."""
    for wanted in candidates:
        series = reducer(rows, wanted)
        if series:
            return series
    return {}


def _matched_pair(
    statements: list[dict], balance: list[dict]
) -> tuple[dict[int, float], dict[int, float]]:
    """Income and equity on the same basis. See the constants above.

    Prefers the parent basis, which is what "return on equity" normally means
    for a listed holder. Falls back to the total basis in one step, taking
    *both* sides with it -- a financial holding that publishes parent income
    but only total equity must not be measured with one of each.
    """
    equity_parent = _year_end(balance, EQUITY_PARENT_TYPE)
    if equity_parent:
        income_parent = _annual_sums(statements, INCOME_PARENT_TYPE)
        if income_parent:
            return income_parent, equity_parent

    equity_total = _year_end(balance, EQUITY_TOTAL_TYPE)
    income_total = _first_series(statements, INCOME_TOTAL_TYPES, _annual_sums)
    return income_total, equity_total


def fetch_annual(sid: str, years: int) -> list[dict]:
    """Annual EPS / net income / equity / ROE for one company, oldest first.

    Two upstream requests. Returns plain dicts rather than `fundamentals.Row`
    so this module stays a client and knows nothing about the table -- the
    caller adapts.
    """
    start = datetime.date(datetime.date.today().year - years, 1, 1)

    statements = _get(STATEMENTS_DATASET, sid, start)
    balance = _get(BALANCE_DATASET, sid, start)

    eps = _annual_sums(statements, EPS_TYPE)
    income, equity = _matched_pair(statements, balance)

    out = []
    for year in sorted(set(eps) | set(income) | set(equity)):
        net_income = income.get(year)
        out.append(
            {
                "year": year,
                "eps": eps.get(year),
                "net_income": net_income,
                "equity": equity.get(year),
                "roe": _roe_percent(
                    net_income, equity.get(year), equity.get(year - 1)
                ),
            }
        )
    # A year with nothing in it is not a year we learned anything about.
    return [r for r in out if any(r[k] is not None for k in ("eps", "net_income", "equity"))]
