"""Listed-company lookup.

The twstock package ships the full TWSE/TPEX listing (~46k rows) and loads it
into memory at import time, so a linear scan is fast enough and there is no
reason to mirror it into PostgreSQL -- we only persist the price history, which
is the part that is large and expensive to fetch.
"""

import twstock

from app.schemas import StockInfo

# Entries whose type is one of these are the ones a user actually wants to chart.
_INTERESTING_TYPES = ("股票", "ETF", "ETN", "受益證券")


def _to_info(row) -> StockInfo:
    return StockInfo(
        code=row.code,
        name=row.name,
        type=row.type,
        market=row.market,
        group=row.group,
        isin=row.ISIN,
        start=row.start,
        data_source=row.data_source,
    )


def get_stock(sid: str) -> StockInfo | None:
    row = twstock.codes.get(sid)
    return _to_info(row) if row else None


def search(query: str, limit: int = 30) -> tuple[int, list[StockInfo]]:
    """Match against code or name. Exact code match always ranks first."""
    q = query.strip()
    if not q:
        return 0, []

    exact: list = []
    code_prefix: list = []
    name_hit: list = []

    for row in twstock.codes.values():
        if row.code == q:
            exact.append(row)
        elif row.code.startswith(q):
            code_prefix.append(row)
        elif q in row.name:
            name_hit.append(row)

    def sort_key(row):
        # Plain equities/ETFs before warrants, then shortest name first: a query
        # like "台積" should surface 台積電 ahead of 台積電元大53購03.
        try:
            type_rank = _INTERESTING_TYPES.index(row.type)
        except ValueError:
            type_rank = len(_INTERESTING_TYPES)
        return (type_rank, 0 if row.name == q else 1, len(row.name), row.code)

    ranked = exact + sorted(code_prefix, key=sort_key) + sorted(name_hit, key=sort_key)
    return len(ranked), [_to_info(r) for r in ranked[:limit]]


def code_count() -> int:
    return len(twstock.codes)
