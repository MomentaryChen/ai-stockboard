"""Per-user 自選股.

The list is small (20 entries at most) and the frontend holds it as a plain
ordered array, so writes replace the whole list rather than patching rows:
`position` is renumbered densely from 0 on every write and there is no gap
management to get wrong.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import WatchlistItem
from app.services import codes as codes_service

logger = logging.getLogger(__name__)

# Mirrors DEFAULT_WATCHLIST in frontend/src/pages/RealtimeBoard.tsx -- keep the
# two in step so a signed-in and a signed-out board start out the same.
DEFAULT_SIDS = ("2330", "2317", "0050")

# Matches the cap the realtime endpoint already applies (routers/realtime.py
# slices to 20) and the .slice(0, 20) in the frontend.
MAX_ITEMS = 20


class WatchlistError(Exception):
    """Base for the failures the router turns into an HTTP status."""


class UnknownStockError(WatchlistError):
    def __init__(self, sid: str):
        super().__init__(f"Stock ID '{sid}' not found")
        self.sid = sid


class TooManyItemsError(WatchlistError):
    def __init__(self) -> None:
        super().__init__(f"A watchlist holds at most {MAX_ITEMS} stocks")


def list_sids(db: Session, user_id: int) -> list[str]:
    stmt = (
        select(WatchlistItem.sid)
        .where(WatchlistItem.user_id == user_id)
        .order_by(WatchlistItem.position)
    )
    return list(db.execute(stmt).scalars())


def _clean(sids: list[str]) -> list[str]:
    """Trim, drop blanks and duplicates, keep the caller's order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in sids:
        sid = raw.strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        cleaned.append(sid)
    return cleaned


def replace(db: Session, user_id: int, sids: list[str]) -> list[str]:
    """Set the whole list. Validates every sid before touching a single row."""
    cleaned = _clean(sids)
    if len(cleaned) > MAX_ITEMS:
        raise TooManyItemsError()

    for sid in cleaned:
        # Resolves indices too -- t00 (大盤) is a watchable sid like any other.
        if codes_service.get_stock(sid) is None:
            raise UnknownStockError(sid)

    db.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user_id))
    db.add_all(
        WatchlistItem(user_id=user_id, sid=sid, position=index)
        for index, sid in enumerate(cleaned)
    )
    db.commit()
    return cleaned


def seed_default(db: Session, user_id: int) -> list[str]:
    """Give a brand-new account the same starting board an anonymous visitor sees."""
    return replace(db, user_id, list(DEFAULT_SIDS))
