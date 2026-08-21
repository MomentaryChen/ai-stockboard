"""Pydantic response models -- the contract the React frontend codes against."""

import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class StockInfo(BaseModel):
    code: str
    name: str
    type: str  # 股票 / ETF / ...
    market: str  # 上市 / 上櫃
    group: str  # 產業別
    isin: str
    start: str  # 上市日
    data_source: Literal["twse", "tpex"]
    # False once the exchange stops listing the code. History, analysis and
    # watchlists still resolve it; search stops offering it.
    is_active: bool = True


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[StockInfo]


class DailyPricePoint(BaseModel):
    date: datetime.date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    change: float | None
    capacity: int | None
    turnover: int | None
    transaction: int | None


class HistoryResponse(BaseModel):
    sid: str
    name: str
    source: Literal["twse", "tpex"]
    months: int
    count: int
    fetched_months: list[str]  # months pulled from upstream on this request
    cached_months: list[str]  # months served straight from PostgreSQL
    data: list[DailyPricePoint]


class DividendEventOut(BaseModel):
    ex_date: datetime.date
    kind: str  # 息 / 權 / 權息
    cash_dividend: float | None
    stock_dividend: float | None
    deduction: float | None  # 權值+息值, the opening gap on the ex-date
    close_before: float | None
    reference_price: float | None
    upcoming: bool


class DividendResponse(BaseModel):
    sid: str
    name: str
    source: Literal["twse", "tpex"]
    # history = yearly TWSE archive; recent = TPEX current window + calendar;
    # none = indices, which do not pay dividends.
    coverage: Literal["history", "recent", "none"]
    years: int
    count: int
    ttm_cash: float | None
    latest_close: float | None
    yield_percent: float | None
    events: list[DividendEventOut]


class MovingAverages(BaseModel):
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None


class BestFourPointResult(BaseModel):
    signal: Literal["buy", "sell", "hold"]
    label: str  # Buy / Sell / Don't touch
    reasons: list[str]


class TraditionalAnalysisResponse(BaseModel):
    """Rule-based technical analysis. The AI engine will return its own shape."""

    sid: str
    name: str
    # Which 四大買賣點 variant produced `best_four_point`: "grs" is the corrected
    # reference behaviour, "twstock" is the library's own (defective) port.
    rule_set: Literal["grs", "twstock"]
    as_of: datetime.date
    sample_size: int
    latest_close: float | None
    moving_averages: MovingAverages
    ma_series: list["MaSeriesPoint"]
    best_four_point: BestFourPointResult


class TraditionalAnalysisSummary(BaseModel):
    """BFP verdict without MA series -- what a watchlist card needs.

    `as_of` is null when we scored from an empty cache (no daily bars yet).
    """

    sid: str
    name: str
    rule_set: Literal["grs", "twstock"]
    as_of: datetime.date | None
    sample_size: int
    latest_close: float | None
    best_four_point: BestFourPointResult


class TraditionalAnalysisBatchResponse(BaseModel):
    items: list[TraditionalAnalysisSummary]
    # Per-sid failures that should not take the rest of the batch down
    # (unknown code, no price rows). Same shape as RealtimeResponse.errors.
    errors: dict[str, str]


class MaSeriesPoint(BaseModel):
    date: datetime.date
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None


class RealtimeQuote(BaseModel):
    code: str
    name: str
    # MIS leaves this null for indices, which have no "full name" field.
    fullname: str | None = None
    time: str
    timestamp: float
    open: float | None
    high: float | None
    low: float | None
    latest_trade_price: float | None
    trade_volume: int | None
    accumulate_trade_volume: int | None
    yesterday_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    best_bid_price: list[float] = []
    best_bid_volume: list[int] = []
    best_ask_price: list[float] = []
    best_ask_volume: list[int] = []


class RealtimeResponse(BaseModel):
    success: bool
    message: str | None = None
    quotes: list[RealtimeQuote]
    errors: dict[str, str] = {}


#: Which way a move went, once a threshold has decided that "barely moved"
#: counts as flat. Composed pairwise on the client: gap x drift is what spells
#: 開高走低 and its siblings, in whichever language is selected.
MoveDirection = Literal["up", "down", "flat"]


class OpenSnapshot(BaseModel):
    """One instrument's opening picture for one trading day.

    `last` is the day's close for a settled session and the current price for
    an intraday one -- the frontend builds the intraday variant from the
    realtime quote, because today's daily bar does not exist until TWSE
    publishes the report. Everything derived from it (`change`, `from_open`)
    follows the same rule, which is why `intraday` is on the wire: the UI has
    to say whether it is showing a close or a tick.
    """

    sid: str
    name: str
    date: datetime.date
    is_index: bool
    intraday: bool

    open: float | None
    prev_close: float | None
    #: 跳空: open - prev_close. The overnight repricing, before this session
    #: traded a single share.
    gap: float | None
    gap_percent: float | None
    gap_direction: MoveDirection

    high: float | None
    low: float | None
    last: float | None
    change: float | None
    change_percent: float | None

    #: last - open. What the session itself did, which the close alone hides:
    #: a day that gaps up and fades ends near flat and looks like a quiet one.
    from_open: float | None
    from_open_percent: float | None
    drift_direction: MoveDirection

    capacity: int | None
    turnover: int | None


class MarketOpenResponse(BaseModel):
    """The index plus the caller's watchlist, all on the same trading day."""

    date: datetime.date
    #: True when `date` is today on the exchange's calendar. The client uses it
    #: to decide whether a live quote may override the settled numbers.
    is_today: bool
    #: True once the requested day's bar exists for the index -- i.e. TWSE has
    #: published the report and the numbers below are final. False all through
    #: the session, and on a holiday.
    settled: bool
    #: Newest index bar in the cache. What to offer when `date` turned out to
    #: be a holiday and there is nothing to show.
    latest_trading_day: datetime.date | None
    items: list[OpenSnapshot]
    #: Per-sid failures -- unknown code, or no cached bar for that day. Same
    #: contract as RealtimeResponse.errors: one bad sid must not drop the rest.
    errors: dict[str, str]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: str
    stock_codes_loaded: int
    # None until `stock_code` has been reconciled with the exchanges at least
    # once -- i.e. the listing on offer is still twstock's bundled snapshot.
    stock_codes_synced_at: datetime.datetime | None = None


class CodeSyncResponse(BaseModel):
    """Outcome of one `stock_code` reconciliation run."""

    # skipped == the stored listing was still inside the sync interval.
    status: Literal["synced", "skipped", "failed"]
    synced_at: datetime.datetime | None
    active: int  # instruments the exchanges currently list
    inserted: int
    updated: int
    delisted: int  # rows retired because the registry no longer carries them
    pruned: int = 0  # expired warrants deleted after their retention window
    message: str | None = None


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------

JobStatus = Literal["success", "skipped", "failed"]
JobTrigger = Literal["startup", "schedule", "manual"]
ScheduleKind = Literal["interval", "daily"]


class JobRunOut(BaseModel):
    """One recorded attempt at one job."""

    id: int
    job_id: str
    started_at: datetime.datetime
    finished_at: datetime.datetime
    duration_seconds: float
    # skipped == the job woke up and correctly had nothing to do.
    status: JobStatus
    trigger: JobTrigger
    # The username that pressed the button; set on manual runs only.
    actor: str | None
    # Job-specific counters. The keys are whatever that job's `stat_labels`
    # declares, which is how one table renders every job.
    stats: dict[str, int]
    message: str | None


class JobScheduleOut(BaseModel):
    """When a job fires, and how far an admin may move it."""

    enabled: bool
    kind: ScheduleKind
    interval_minutes: int
    daily_at: str  # "HH:MM" in `timezone`
    timezone: str
    # Guard rails from the job definition, sent so the UI can bound its own
    # input instead of guessing -- the server refuses out-of-range values
    # regardless.
    min_interval_minutes: int
    max_interval_minutes: int
    # False once an admin has saved anything: from then on the row wins over
    # the environment variables that seeded it.
    is_default: bool
    updated_at: datetime.datetime | None
    updated_by: str | None


class JobOut(BaseModel):
    """A job as the admin console needs it: what it is, when, and how it went."""

    id: str
    name: str
    description: str
    schedule: JobScheduleOut
    # True while an attempt is in flight *in this process*. The run-now button
    # is disabled on it, and the server refuses a second run anyway.
    running: bool
    expected_seconds: int
    manual_cooldown_seconds: int
    stat_labels: dict[str, str]
    last_run: JobRunOut | None
    # Newest run that did the work. A long gap here with recent `skipped` runs
    # means the schedule is alive but the output is stale.
    last_success_at: datetime.datetime | None
    next_run_at: datetime.datetime | None
    total_runs: int


class JobListResponse(BaseModel):
    # False when JOBS_SCHEDULER_ENABLED is off: every job below is then
    # manual-only, which is otherwise indistinguishable from a stuck scheduler.
    scheduler_enabled: bool
    timezone: str
    jobs: list[JobOut]


class JobRunsResponse(BaseModel):
    job: JobOut
    total: int  # attempts on record, which the rolling window caps
    runs: list[JobRunOut]


class JobScheduleUpdateRequest(BaseModel):
    """A partial edit. Unset fields keep their current value."""

    enabled: bool | None = None
    kind: ScheduleKind | None = None
    # Bounds are per job and enforced server-side; this only rejects the
    # nonsense that never reaches a job (0, negatives, a year).
    interval_minutes: int | None = Field(None, ge=1, le=525_600)
    daily_at: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class JobTriggerResponse(BaseModel):
    """Answer to the run-now button.

    The run itself happens on a background thread -- the listing sync takes ~40
    seconds and the page lists several jobs -- so this only confirms it started.
    """

    job_id: str
    started: bool
    message: str


# --------------------------------------------------------------------------
# Accounts, tokens and watchlists
# --------------------------------------------------------------------------

Role = Literal["ADMIN", "USER"]


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    phone: str | None
    role: Role
    is_active: bool
    # True between an ADMIN password reset and the user choosing their own
    # password. While set, the API allows only /api/auth/me and
    # /api/auth/me/password, and the UI keeps them on the change-password page.
    must_change_password: bool
    created_at: datetime.datetime


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    phone: str | None = None


class LoginRequest(BaseModel):
    # Accepts either the username or the email -- the UI labels it 帳號或 Email.
    identifier: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds the access token stays valid
    user: UserOut


class ProfileUpdateRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class UserListResponse(BaseModel):
    total: int
    users: list[UserOut]


class UserUpdateRequest(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetResponse(BaseModel):
    """The outcome of an ADMIN-initiated reset.

    `temp_password` is the only time the generated password exists outside the
    bcrypt hash -- it is not stored and cannot be read back, so the admin has
    to relay it before leaving the page.
    """

    user: UserOut
    temp_password: str


class WatchlistResponse(BaseModel):
    count: int
    sids: list[str]


class WatchlistUpdateRequest(BaseModel):
    sids: list[str]
