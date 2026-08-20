"""Pydantic response models -- the contract the React frontend codes against."""

import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


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


class SyncRun(BaseModel):
    """One recorded attempt at reconciling `stock_code`."""

    id: int
    started_at: datetime.datetime
    finished_at: datetime.datetime
    duration_seconds: float
    status: Literal["synced", "skipped", "failed"]
    trigger: Literal["startup", "schedule", "manual"]
    sources: list[str]  # markets that answered: twse / tpex
    active: int
    inserted: int
    updated: int
    delisted: int
    pruned: int
    message: str | None


class SyncRunsResponse(BaseModel):
    """The batch job's health, as the admin view needs it."""

    # Whether the scheduler is running at all, and how often. Without these a
    # long gap between runs is ambiguous: broken, or simply switched off?
    enabled: bool
    interval_hours: int
    last_success_at: datetime.datetime | None
    # None when `stock_code` has never been reconciled -- the listing on offer
    # is still twstock's bundled snapshot.
    synced_at: datetime.datetime | None
    active: int
    total: int  # attempts on record, which the rolling window caps
    runs: list[SyncRun]


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


class WatchlistResponse(BaseModel):
    count: int
    sids: list[str]


class WatchlistUpdateRequest(BaseModel):
    sids: list[str]
