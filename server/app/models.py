"""Database tables.

`stock_code` mirrors the exchanges' ISIN registry -- every instrument they
list, and the gate every other lookup passes through.
`daily_price` holds one row per (stock, trading day) -- the market history this
service owns, and what every analysis engine reads from.
`fetch_log` records which (stock, year, month) buckets have already been pulled
from the exchange, which is what lets us skip the network on repeat requests.
`dividend_event` is one ex-right / ex-dividend day per stock; `dividend_fetch_log`
is the same kind of bucket stamp `fetch_log` is, so a year of TWSE events is
pulled once.

`job_schedule` and `job_run` carry the background jobs: when each one is meant
to fire (admin-editable, which is why it is a table and not just an env var)
and what happened every time it did. The run log is the only way to tell a
healthy nightly job from one that has been failing quietly, because both leave
the tables they maintain untouched.

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# Rows copied from twstock's bundled CSV snapshot carry this stamp instead of
# the time they were written. It marks the table as never having been
# reconciled with the exchanges, so the first real sync runs immediately rather
# than waiting out the interval, and /api/health reports "never synced" rather
# than a date that would imply the listing is current.
SEED_SYNCED_AT = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

# How many attempts `job_run` keeps *per job*. Enough to cover a couple of
# months of a daily job plus any manual runs, and small enough that the admin
# view never needs pagination.
MAX_JOB_RUNS_KEPT = 200


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


# Every background job is scheduled one of two ways. `interval` fires every N
# minutes from the last attempt; `daily` fires at a wall-clock time, which is
# what you actually want for a market job -- "02:30 每天" survives restarts,
# whereas "every 24h" drifts to whenever the container last booted.
SCHEDULE_INTERVAL = "interval"
SCHEDULE_DAILY = "daily"
SCHEDULE_KINDS = (SCHEDULE_INTERVAL, SCHEDULE_DAILY)

JOB_STATUSES = ("success", "skipped", "failed")
JOB_TRIGGERS = ("startup", "schedule", "manual")


class JobSchedule(Base):
    """When one background job fires. Written by admins, read by the scheduler.

    The job *definitions* live in code (`app/services/jobs/registry.py`); this
    table only carries the parts an operator is allowed to change, and only
    once they have changed something -- a job with no row here runs on the
    defaults its definition declares.

    Deliberately the source of truth over the environment variables that seed
    it: a schedule edited in the UI has to survive a container restart, and an
    env var cannot be written back to from a running process. The env values
    stay meaningful as the first-boot defaults.

    `updated_by` stores the username as text rather than a foreign key. It is
    an audit trail, and it must still read correctly after that account is
    deleted.
    """

    __tablename__ = "job_schedule"

    # Matches JobDefinition.id -- an identifier from code, never user input.
    job_id: Mapped[str] = mapped_column(String(48), primary_key=True)

    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    kind: Mapped[str] = mapped_column(
        String(16), default=SCHEDULE_INTERVAL, server_default=SCHEDULE_INTERVAL
    )
    # Used when kind == interval. Bounds are enforced per job by the registry,
    # not here: a five-minute listing sync would get us banned by TWSE, while
    # five minutes is perfectly reasonable for a cleanup job.
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    # Used when kind == daily: "HH:MM" in `SCHEDULER_TIMEZONE`. Stored as text
    # because it is a wall-clock time with no date, and Time columns drag
    # timezone semantics along that we would only have to strip again.
    daily_at: Mapped[str] = mapped_column(String(5), default="03:00")

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(
            "kind in ('interval', 'daily')", name="ck_job_schedule_kind"
        ),
        CheckConstraint("interval_minutes > 0", name="ck_job_schedule_interval"),
    )


class JobRun(Base):
    """One attempt at one background job, successful or not.

    Without this the batch jobs are invisible: a listing sync that has been
    failing for a fortnight looks exactly like one that had nothing to do,
    because both leave `stock_code` untouched. Skipped runs are recorded too --
    they are the heartbeat that says the scheduler is still alive.

    `stats` is JSONB rather than columns because every job counts different
    things (the sync counts 新增/下市, a cleanup counts deleted rows), and the
    admin table renders whatever keys the job's definition declares labels for.

    `actor` is the username that pressed the button, kept for manual runs only.
    Together with `job_schedule.updated_by` it answers "who made this job do
    that", which is the question an audit of an admin-only feature asks.

    Trimmed to the most recent `MAX_JOB_RUNS_KEPT` rows *per job* on every
    write, so it stays a rolling window rather than a table nobody ever prunes.
    """

    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(48))

    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(16))  # success / skipped / failed
    trigger: Mapped[str] = mapped_column(String(16))  # startup / schedule / manual
    actor: Mapped[str | None] = mapped_column(String(32))

    # {"inserted": 12, "delisted": 3, ...} -- keys are job-specific.
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    message: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        # The admin view reads one job's newest rows; the scheduler reads the
        # single newest row to work out when the next fire is due.
        Index("ix_job_run_job_started", "job_id", "started_at"),
        CheckConstraint(
            "status in ('success', 'skipped', 'failed')", name="ck_job_run_status"
        ),
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


class DividendEvent(Base):
    """One ex-right / ex-dividend day for one stock.

    TWSE publishes a yearly JSON of every listed name that went ex (TWT49U),
    so one upstream call fills this table for the whole board. TPEX only
    publishes the current window plus the announcement calendar, which is why
    OTC history here is recent rather than complete.
    """

    __tablename__ = "dividend_event"

    sid: Mapped[str] = mapped_column(String(16), primary_key=True)
    ex_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)

    name: Mapped[str] = mapped_column(String(64), default="")
    # 息 / 權 / 權息 -- normalised from TWSE's 權/息 and TPEX's 除息/除權.
    kind: Mapped[str] = mapped_column(String(8), default="")
    close_before: Mapped[float | None] = mapped_column(Numeric(16, 6))
    reference_price: Mapped[float | None] = mapped_column(Numeric(16, 6))
    deduction: Mapped[float | None] = mapped_column(Numeric(16, 8))  # 權值+息值
    cash_dividend: Mapped[float | None] = mapped_column(Numeric(16, 8))
    stock_dividend: Mapped[float | None] = mapped_column(Numeric(16, 8))
    source: Mapped[str] = mapped_column(String(8), default="twse")

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DividendFetchLog(Base):
    """Cache stamp for one dividend upstream pull.

    TWSE is keyed by calendar year (`2024`); TPEX is a single `live` bucket
    because the exchange does not offer a historical range query.
    """

    __tablename__ = "dividend_fetch_log"

    source: Mapped[str] = mapped_column(String(8), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(16), primary_key=True)

    row_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Role names are a closed set, but stay a plain String column guarded by a check
# constraint. A native PG enum would need an ALTER TYPE migration to gain a
# value, and ALTER TYPE ... ADD VALUE cannot run inside a transaction block --
# so the one change this column is ever likely to want would be the one kind of
# migration that cannot be applied atomically at startup. Editing a check
# constraint can.
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

    # bcrypt produces 60 chars. The extra room means switching algorithm is a
    # code change rather than a code change plus a migration that rewrites every
    # row of the table while people are trying to sign in.
    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER, server_default=ROLE_USER)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )

    # Set when an ADMIN resets the password to a generated one, cleared the
    # moment the user picks their own. While it is true the account may only
    # read its own profile and change its password -- `deps.get_current_user`
    # refuses everything else -- so a temporary password handed over in chat
    # cannot be used to browse the account.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    # Raised by self-service registration while REGISTRATION_REQUIRES_APPROVAL
    # is on, and cleared by the ADMIN who activates the account.
    #
    # It exists to tell two dormant accounts apart, because `is_active = false`
    # alone cannot: an account waiting for its first review and an account an
    # admin suspended look identical, and they need different words in the UI
    # and different actions from the operator. It also keeps the distinction
    # honest across restarts -- deriving "never reviewed" from the absence of
    # refresh tokens or from created_at would guess wrong the moment either of
    # those is cleaned up.
    #
    # The default is false, which is what makes this safe to add to a table
    # that already holds rows: every existing account is one an admin already
    # lives with, not one waiting in a queue nobody knew existed.
    pending_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    # Consecutive failed sign-ins, and the instant this account starts
    # answering again. Both are reset by any successful sign-in.
    #
    # In the database rather than in process memory on purpose: a lockout that
    # a container restart clears is a lockout an attacker can clear, and
    # `docker compose restart` is not a privileged operation for whoever is
    # already grinding the login endpoint. The per-IP half of the same defence
    # is in-process precisely because it does not have that property -- see
    # services/login_guard.py.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
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
