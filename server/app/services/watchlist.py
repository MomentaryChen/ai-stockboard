"""Per-user 自選股.

The list is small (20 entries at most) and the frontend holds it as a plain
ordered array, so writes replace the whole list rather than patching rows:
`position` is renumbered densely from 0 on every write and there is no gap
management to get wrong.

Groups are named folders on that same list, not extra lists. The realtime
endpoint already caps a quote at 20 sids; a second independent watchlist would
blow the budget. A stock belongs to at most one group. Replacing the sid list
keeps the assignment of every sid that stays on it, so adding a stock cannot
scatter the folders the user already made.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import WatchlistGroup, WatchlistItem
from app.services import codes as codes_service

logger = logging.getLogger(__name__)

# Mirrors DEFAULT_WATCHLIST in frontend/src/pages/RealtimeBoard.tsx -- keep the
# two in step so a signed-in and a signed-out board start out the same.
DEFAULT_SIDS = ("2330", "2317", "0050")

# Matches the cap the realtime endpoint already applies (routers/realtime.py
# slices to 20) and the .slice(0, 20) in the frontend.
MAX_ITEMS = 20

# Ten folders is plenty for 20 stocks; the UI is a chip row, not a tree.
MAX_GROUPS = 10
MAX_GROUP_NAME = 20


class WatchlistError(Exception):
    """Base for the failures the router turns into an HTTP status."""


class UnknownStockError(WatchlistError):
    def __init__(self, sid: str):
        super().__init__(f"Stock ID '{sid}' not found")
        self.sid = sid


class TooManyItemsError(WatchlistError):
    def __init__(self) -> None:
        super().__init__(f"A watchlist holds at most {MAX_ITEMS} stocks")


class UnknownGroupError(WatchlistError):
    def __init__(self, group_id: int):
        super().__init__(f"Group '{group_id}' not found")
        self.group_id = group_id


class InvalidGroupNameError(WatchlistError):
    def __init__(self) -> None:
        super().__init__(f"Group name must be 1 to {MAX_GROUP_NAME} characters")


class DuplicateGroupNameError(WatchlistError):
    def __init__(self, name: str):
        super().__init__(f"A group named '{name}' already exists")
        self.name = name


class TooManyGroupsError(WatchlistError):
    def __init__(self) -> None:
        super().__init__(f"A watchlist holds at most {MAX_GROUPS} groups")


@dataclass(frozen=True)
class GroupState:
    id: int
    name: str
    position: int


@dataclass(frozen=True)
class WatchlistState:
    sids: tuple[str, ...]
    groups: tuple[GroupState, ...]
    group_by_sid: dict[str, int]


def load(db: Session, user_id: int) -> WatchlistState:
    sids = tuple(
        db.execute(
            select(WatchlistItem.sid)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.position)
        ).scalars()
    )
    groups = tuple(
        GroupState(id=row.id, name=row.name, position=row.position)
        for row in db.execute(
            select(WatchlistGroup)
            .where(WatchlistGroup.user_id == user_id)
            .order_by(WatchlistGroup.position, WatchlistGroup.id)
        ).scalars()
    )
    assigned = {
        row.sid: row.group_id
        for row in db.execute(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.group_id.is_not(None),
            )
        ).scalars()
        if row.group_id is not None
    }
    return WatchlistState(sids=sids, groups=groups, group_by_sid=assigned)


def list_sids(db: Session, user_id: int) -> list[str]:
    return list(load(db, user_id).sids)


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


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_GROUP_NAME:
        raise InvalidGroupNameError()
    return cleaned


def _owned_group_ids(db: Session, user_id: int) -> set[int]:
    return set(
        db.execute(
            select(WatchlistGroup.id).where(WatchlistGroup.user_id == user_id)
        ).scalars()
    )


def _require_group(db: Session, user_id: int, group_id: int) -> WatchlistGroup:
    row = db.get(WatchlistGroup, group_id)
    if row is None or row.user_id != user_id:
        raise UnknownGroupError(group_id)
    return row


def _ensure_unique_name(
    db: Session, user_id: int, name: str, *, excluding_id: int | None = None
) -> None:
    stmt = select(WatchlistGroup.id).where(
        WatchlistGroup.user_id == user_id,
        WatchlistGroup.name == name,
    )
    if excluding_id is not None:
        stmt = stmt.where(WatchlistGroup.id != excluding_id)
    if db.execute(stmt).scalar() is not None:
        raise DuplicateGroupNameError(name)


def replace(
    db: Session,
    user_id: int,
    sids: list[str],
    group_by_sid: dict[str, int | None] | None = None,
) -> WatchlistState:
    """Set the whole list. Validates every sid before touching a single row.

    Assignments for sids that remain are kept, then `group_by_sid` is applied
    on top. That is what lets "add this stock to the group I am looking at"
    be one write without the client re-sending every other assignment.
    """
    cleaned = _clean(sids)
    if len(cleaned) > MAX_ITEMS:
        raise TooManyItemsError()

    for sid in cleaned:
        # Resolves indices too -- t00 (大盤) is a watchable sid like any other.
        if codes_service.get_stock(sid) is None:
            raise UnknownStockError(sid)

    previous = {
        row.sid: row.group_id
        for row in db.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == user_id)
        ).scalars()
    }
    owned = _owned_group_ids(db, user_id)
    if group_by_sid:
        staying = set(cleaned)
        for sid, group_id in group_by_sid.items():
            if sid not in staying:
                continue
            if group_id is not None and group_id not in owned:
                raise UnknownGroupError(group_id)
            previous[sid] = group_id

    assignments: dict[str, int | None] = {}
    for sid in cleaned:
        group_id = previous.get(sid)
        if group_id is not None and group_id not in owned:
            # The group was deleted between reads; dropping the label is the
            # same outcome ON DELETE SET NULL would have produced.
            group_id = None
        assignments[sid] = group_id

    db.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user_id))
    db.add_all(
        WatchlistItem(
            user_id=user_id,
            sid=sid,
            position=index,
            group_id=assignments.get(sid),
        )
        for index, sid in enumerate(cleaned)
    )
    db.commit()
    return load(db, user_id)


def seed_default(db: Session, user_id: int) -> list[str]:
    """Give a brand-new account the same starting board an anonymous visitor sees."""
    return list(replace(db, user_id, list(DEFAULT_SIDS)).sids)


def create_group(db: Session, user_id: int, name: str) -> WatchlistState:
    cleaned = _clean_name(name)
    _ensure_unique_name(db, user_id, cleaned)
    count = db.execute(
        select(func.count())
        .select_from(WatchlistGroup)
        .where(WatchlistGroup.user_id == user_id)
    ).scalar_one()
    if count >= MAX_GROUPS:
        raise TooManyGroupsError()
    highest = db.execute(
        select(func.max(WatchlistGroup.position)).where(
            WatchlistGroup.user_id == user_id
        )
    ).scalar()
    db.add(
        WatchlistGroup(
            user_id=user_id,
            name=cleaned,
            position=0 if highest is None else highest + 1,
        )
    )
    db.commit()
    return load(db, user_id)


def rename_group(
    db: Session, user_id: int, group_id: int, name: str
) -> WatchlistState:
    row = _require_group(db, user_id, group_id)
    cleaned = _clean_name(name)
    if cleaned != row.name:
        _ensure_unique_name(db, user_id, cleaned, excluding_id=row.id)
        row.name = cleaned
        db.commit()
    return load(db, user_id)


def delete_group(db: Session, user_id: int, group_id: int) -> WatchlistState:
    row = _require_group(db, user_id, group_id)
    db.delete(row)
    db.commit()
    return load(db, user_id)
