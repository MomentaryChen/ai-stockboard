"""Database tables.

`daily_price` holds one row per (stock, trading day) -- the market history this
service owns, and what every analysis engine reads from.
`fetch_log` records which (stock, year, month) buckets have already been pulled
from the exchange, which is what lets us skip the network on repeat requests.
"""

import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
