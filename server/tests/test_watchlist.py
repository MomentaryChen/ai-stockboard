"""Watchlist replace, and the named folders that partition the same list."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import AppUser, WatchlistGroup, WatchlistItem
from app.services import watchlist as watchlist_service
from app.services.watchlist import (
    DuplicateGroupNameError,
    InvalidGroupNameError,
    TooManyGroupsError,
    TooManyItemsError,
    UnknownGroupError,
    UnknownStockError,
)
from tests.helpers import add_user


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    AppUser.__table__.create(engine)
    WatchlistGroup.__table__.create(engine)
    WatchlistItem.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def user(db: Session) -> AppUser:
    return add_user(db)


@pytest.fixture
def known_stocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watchlist_service.codes_service,
        "get_stock",
        lambda sid: object() if sid else None,
    )


def test_replace_round_trips_the_list(db: Session, user: AppUser, known_stocks: None):
    state = watchlist_service.replace(db, user.id, ["2330", "2317", "0050"])
    assert state.sids == ("2330", "2317", "0050")
    assert state.groups == ()
    assert state.group_by_sid == {}


def test_replace_drops_duplicates_and_blanks(
    db: Session, user: AppUser, known_stocks: None
):
    state = watchlist_service.replace(db, user.id, [" 2330 ", "2330", "", "2317"])
    assert state.sids == ("2330", "2317")


def test_replace_rejects_unknown_sids_without_writing(
    db: Session, user: AppUser, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        watchlist_service.codes_service,
        "get_stock",
        lambda sid: object() if sid == "2330" else None,
    )
    with pytest.raises(UnknownStockError, match="9999"):
        watchlist_service.replace(db, user.id, ["2330", "9999"])
    assert watchlist_service.load(db, user.id).sids == ()


def test_replace_rejects_more_than_the_cap(
    db: Session, user: AppUser, known_stocks: None
):
    too_many = [f"{i:04d}" for i in range(watchlist_service.MAX_ITEMS + 1)]
    with pytest.raises(TooManyItemsError):
        watchlist_service.replace(db, user.id, too_many)


def test_replace_keeps_the_group_of_sids_that_stay(
    db: Session, user: AppUser, known_stocks: None
):
    watchlist_service.replace(db, user.id, ["2330", "2317", "0050"])
    created = watchlist_service.create_group(db, user.id, "核心")
    group_id = created.groups[0].id
    watchlist_service.replace(
        db, user.id, ["2330", "2317", "0050"], {"2330": group_id, "2317": group_id}
    )

    state = watchlist_service.replace(db, user.id, ["2317", "0050", "2881"])
    assert state.sids == ("2317", "0050", "2881")
    assert state.group_by_sid == {"2317": group_id}


def test_replace_overlay_assigns_and_ungroups(
    db: Session, user: AppUser, known_stocks: None
):
    watchlist_service.replace(db, user.id, ["2330", "2317"])
    group_id = watchlist_service.create_group(db, user.id, "半導體").groups[0].id
    watchlist_service.replace(db, user.id, ["2330", "2317"], {"2330": group_id})

    state = watchlist_service.replace(
        db, user.id, ["2330", "2317"], {"2330": None, "2317": group_id}
    )
    assert state.group_by_sid == {"2317": group_id}


def test_replace_overlay_rejects_someone_elses_group(
    db: Session, user: AppUser, known_stocks: None
):
    other = add_user(db, user_id=2, username="bob", email="bob@example.com")
    watchlist_service.replace(db, user.id, ["2330"])
    foreign = watchlist_service.create_group(db, other.id, "不是你的").groups[0].id
    with pytest.raises(UnknownGroupError):
        watchlist_service.replace(db, user.id, ["2330"], {"2330": foreign})
    assert watchlist_service.load(db, user.id).group_by_sid == {}


def test_create_rename_delete_group(db: Session, user: AppUser, known_stocks: None):
    watchlist_service.replace(db, user.id, ["2330", "2317"])
    created = watchlist_service.create_group(db, user.id, "  半導體  ")
    assert [group.name for group in created.groups] == ["半導體"]
    group_id = created.groups[0].id

    assigned = watchlist_service.replace(
        db, user.id, ["2330", "2317"], {"2330": group_id}
    )
    assert assigned.group_by_sid == {"2330": group_id}

    renamed = watchlist_service.rename_group(db, user.id, group_id, "科技")
    assert [group.name for group in renamed.groups] == ["科技"]
    assert renamed.group_by_sid == {"2330": group_id}

    deleted = watchlist_service.delete_group(db, user.id, group_id)
    assert deleted.groups == ()
    assert deleted.sids == ("2330", "2317")
    assert deleted.group_by_sid == {}


def test_duplicate_and_invalid_names(db: Session, user: AppUser):
    watchlist_service.create_group(db, user.id, "核心")
    with pytest.raises(DuplicateGroupNameError, match="核心"):
        watchlist_service.create_group(db, user.id, "核心")
    with pytest.raises(InvalidGroupNameError):
        watchlist_service.create_group(db, user.id, "   ")
    with pytest.raises(InvalidGroupNameError):
        watchlist_service.create_group(db, user.id, "x" * 21)
    # Renaming to the same name is a no-op, not a unique-constraint fight.
    group_id = watchlist_service.load(db, user.id).groups[0].id
    same = watchlist_service.rename_group(db, user.id, group_id, "核心")
    assert same.groups[0].name == "核心"


def test_group_cap(db: Session, user: AppUser):
    for index in range(watchlist_service.MAX_GROUPS):
        watchlist_service.create_group(db, user.id, f"g{index}")
    with pytest.raises(TooManyGroupsError):
        watchlist_service.create_group(db, user.id, "one-more")


def test_unknown_group_is_not_found(db: Session, user: AppUser):
    with pytest.raises(UnknownGroupError):
        watchlist_service.rename_group(db, user.id, 99, "nope")
    with pytest.raises(UnknownGroupError):
        watchlist_service.delete_group(db, user.id, 99)
