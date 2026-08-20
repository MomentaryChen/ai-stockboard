"""Database tables.

`stock_code` mirrors the exchanges' ISIN registry -- every instrument they
list, and the gate every other lookup passes through.
`daily_price` holds one row per (stock, trading day) -- the market history this
service owns, and what every analysis engine reads from.
`fetch_log` records which (stock, year, month) buckets have already been pulled
from the exchange, which is what lets us skip the network on repeat requests.

`app_user`, `refresh_token` and `watchlist_item` carry the account system: who
may sign in, which refresh tokens are still live, and what each user watches.
"""

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# Rows copied from twstock's bundled CSV snapshot carry this stamp instead of
# the time they were written. It marks the table as never having been
# reconciled with the exchanges, so the first real sync runs immediately rather
# than waiting out the interval, and /api/health reports "never synced" rather
# than a date that would imply the listing is current.
SEED_SYNCED_AT = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


class StockCode(Base):
    """Every instrument TWSE and TPEX list, mirrored from their ISIN registry.

    twstock ships this listing as CSVs baked into its package and refreshes them
    only by rewriting those files in place -- which a container cannot do, so
    the bundled copy silently ages and any code listed after it answers 404 as
    though it never existed. Owning the table here is what lets
    `app/services/code_sync.py` keep it current.

    Rows are retired, never deleted: `daily_price` and `watchlist_item` both
    reference `code`, and a delisted company's history is still worth reading.
    """

    __tablename__ = "stock_code"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)

    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))  # 股票 / ETF / 上市認購(售)權證 ...
    market: Mapped[str] = mapped_column(String(16))  # 上市 / 上櫃
    group: Mapped[str] = mapped_column(String(64), default="")  # 產業別
    isin: Mapped[str] = mapped_column(String(24), default="")
    # Left as the exchange's own "YYYY/MM/DD" string rather than a Date: it is
    # only ever displayed, and the registry carries future-dated and blank
    # values that a Date column would reject outright.
    start: Mapped[str] = mapped_column(String(16), default="")
    data_source: Mapped[str] = mapped_column(String(8))  # twse / tpex

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    delisted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    # Stamped with the start of the sync run that last saw this code upstream.
    # That is what lets a run retire everything it did not see without building
    # an IN clause around 44k codes.
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # The search cache reads every live instrument on load; the sync reads
        # the same set to work out what to retire.
        Index("ix_stock_code_active", "is_active"),
    )


class DailyPrice(Base):
    __tablename__ = "daily_price"

    sid: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)

    capacity: Mapped[int | None] = mapped_column(BigInteger)  # 成交股數
    turnover: Mapped[int | None] = mapped_column(BigInteger)  # 成交金額
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    change: Mapped[float | None] = mapped_column(Numeric(12, 4))
    transaction: Mapped[int | None] = mapped_column(Integer)  # 成交筆數
    note: Mapped[str | None] = mapped_column(String(64))

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_daily_price_sid_date", "sid", "date"),)


class FetchLog(Base):
    """One row per (stock, month) successfully fetched from the upstream source."""

    __tablename__ = "fetch_log"

    sid: Mapped[str] = mapped_column(String(16), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)

    row_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(8), default="twse")
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Role names are a closed set, but stay a plain String column guarded by a check
# constraint: `create_all` can create a native PG enum but can never ALTER TYPE
# it afterwards, and this project has no migration tool.
ROLE_ADMIN = "ADMIN"
ROLE_USER = "USER"
ROLES = (ROLE_ADMIN, ROLE_USER)


class AppUser(Base):
    """An account.

    Named `app_user` rather than `user` because `user` is reserved in
    PostgreSQL -- and it fails *silently*: `select * from user` returns the
    session user, not this table.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    username: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))

    # bcrypt produces 60 chars; the extra room is because create_all never
    # migrates an existing table, so changing algorithm later would need manual
    # SQL if this were sized exactly.
    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER, server_default=ROLE_USER)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Functional unique indexes: the display casing the user typed is kept,
        # but `Victor` and `victor` cannot both register. Every lookup must go
        # through func.lower() on both sides or it will not use these.
        Index("ux_app_user_username_lower", text("lower(username)"), unique=True),
        Index("ux_app_user_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("role in ('ADMIN', 'USER')", name="ck_app_user_role"),
    )


class RefreshToken(Base):
    """One row per issued refresh token.

    The token itself is opaque and never stored -- only its SHA-256 hex digest,
    which is unique by construction and so doubles as the primary key. That
    keeps the house style of natural keys and means a leaked dump yields
    nothing usable. `/api/auth/refresh` rotates: it revokes the row it was
    handed and inserts a fresh one.
    """

    __tablename__ = "refresh_token"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE")
    )

    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_refresh_token_user_expires", "user_id", "expires_at"),)


class WatchlistItem(Base):
    """One stock on one user's 自選股.

    (user_id, sid) is naturally unique, so it doubles as the primary key and
    dedupes for free -- the same shape `daily_price` uses. `position` preserves
    the order the user arranged them in.

    Rows are cleaned up by the database's ON DELETE CASCADE; deliberately no
    relationship() is declared, matching the rest of this module. If one is ever
    added it needs passive_deletes=True, or SQLAlchemy will try to NULL the
    non-nullable FK first.
    """

    __tablename__ = "watchlist_item"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    sid: Mapped[str] = mapped_column(String(16), primary_key=True)

    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_watchlist_item_user_position", "user_id", "position"),)
