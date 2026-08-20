"""Pydantic response models -- the contract the React frontend codes against."""

import datetime
from typing import Literal

from pydantic import BaseModel


class StockInfo(BaseModel):
    code: str
    name: str
    type: str  # 股票 / ETF / ...
    market: str  # 上市 / 上櫃
    group: str  # 產業別
    isin: str
    start: str  # 上市日
    data_source: Literal["twse", "tpex"]


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
    fullname: str
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
