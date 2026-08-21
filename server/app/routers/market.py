"""大盤 / 當日開盤情報.

One endpoint, `GET /api/market/open`, answering "how did this trading day
start" for the index and for whatever the caller is watching. Public, like
history and the rule analysis, because it is served out of the PostgreSQL
cache -- the one market-data route that reaches upstream, and so the one behind
a sign-in, is `/api/realtime`.
"""

import datetime
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import MarketOpenResponse
from app.services import history as history_service
from app.services import market_index
from app.services import market_open as market_open_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market"])

#: Same cap as /api/realtime and the batch analysis: a watchlist is bounded, and
#: so is the response.
MAX_SIDS = 20


def _backfill_index(db: Session, day: datetime.date) -> bool:
    """Pull the index's own bars for `day`'s month, and the month before it.

    The only upstream trip this endpoint makes, and only for the index, and only
    when the cache could not answer for a session that has already happened. It
    exists because the date picker reaches further back than the chart's
    1/3/6/12-month ranges do, so a date nobody has charted has no cached bar --
    and answering "no data" for the board's own subject would be wrong rather
    than merely thin.

    The previous month is fetched too: the first session of a month needs the
    bar before it to have a previous close.

    Bounded to one sid and two months, recorded in `fetch_log`, so this is a
    no-op once warm. Watchlist sids stay cache-only on purpose -- see the note
    on `market_open.snapshots`.
    """
    first_of_month = day.replace(day=1)
    previous_month = first_of_month - datetime.timedelta(days=1)
    buckets = sorted({(previous_month.year, previous_month.month), (day.year, day.month)})
    try:
        history_service.ensure_months(db, market_index.DEFAULT_INDEX, buckets)
    except Exception:
        # An upstream hiccup degrades the board to whatever is cached. It must
        # not 500 the dashboard's opening view, which is the landing page.
        logger.warning("index backfill failed for %s", day, exc_info=True)
        db.rollback()
        return False
    return True


@router.get("/open", response_model=MarketOpenResponse)
def get_market_open(
    date: datetime.date | None = Query(
        None,
        description="交易日，預設為台北時間的今天。非交易日會回傳空的 items 與 latest_trading_day",
    ),
    sids: str = Query("", description="除大盤外要一併查詢的代碼，逗號分隔，例如 2330,0050"),
    db: Session = Depends(get_db),
) -> MarketOpenResponse:
    today = market_open_service.market_today()
    day = date or today

    requested = [s.strip() for s in sids.split(",") if s.strip()]
    # The index leads: it is the page's subject, and `snapshots` reports the
    # latest cached trading day off whichever sid comes first.
    index_sid = market_index.DEFAULT_INDEX
    ordered = [index_sid]
    ordered += [s for s in dict.fromkeys(requested) if s != index_sid][:MAX_SIDS]

    items, errors, latest = market_open_service.snapshots(db, ordered, day)

    def _has_index() -> bool:
        return any(item.sid == index_sid for item in items)

    # Today is deliberately excluded: its bar does not exist upstream either
    # until TWSE publishes the day's report, so fetching would burn rate-limit
    # budget to learn nothing. The client covers today with the live quote, and
    # falls back to the chart's own last bar once the report lands.
    if not _has_index() and day < today and _backfill_index(db, day):
        items, errors, latest = market_open_service.snapshots(db, ordered, day)

    return MarketOpenResponse(
        date=day,
        is_today=day == today,
        settled=_has_index(),
        latest_trading_day=latest,
        items=items,
        errors=errors,
    )
