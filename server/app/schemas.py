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


class ChipFlow(BaseModel):
    """One institutional column's recent picture, in shares.

    `streak` is consecutive same-sign days counted back from the newest row
    that has a figure -- not a Buy/Sell call, and not mixed into 四大買賣點.
    """

    streak: Literal["buy", "sell", "none"]
    streak_days: int
    net_5d: int | None


class ChipDayOut(BaseModel):
    date: datetime.date
    foreign_net: int | None
    trust_net: int | None
    dealer_net: int | None
    total_net: int | None
    margin_balance: int | None
    margin_change: int | None
    short_balance: int | None
    short_change: int | None


class ChipResponse(BaseModel):
    sid: str
    name: str
    source: Literal["twse", "tpex"]
    # daily = the all-market reports; none = indices, which have no per-name chip.
    coverage: Literal["daily", "none"]
    days: int
    as_of: datetime.date | None
    count: int
    fetched_dates: list[str]
    cached_dates: list[str]
    foreign: ChipFlow
    trust: ChipFlow
    dealer: ChipFlow
    total: ChipFlow
    margin_balance: int | None
    margin_change: int | None
    short_balance: int | None
    short_change: int | None
    rows: list[ChipDayOut]


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


# --- AI analysis -------------------------------------------------------------
#
# The rule engine above answers buy/sell/hold. The AI engine answers a *position*
# question -- get in, get out, or leave it alone, and at what size -- so it needs
# its own vocabulary rather than an extra label on BestFourPointResult.


class MaFeatures(BaseModel):
    """Moving averages plus where the close sits relative to each one.

    The percentages matter more to a language model than the raw averages: a
    prompt that says "close is 3.2% above MA20" needs no arithmetic done on it,
    and models are markedly worse at arithmetic than at reading a number.
    """

    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None
    close_vs_ma5_pct: float | None
    close_vs_ma20_pct: float | None
    close_vs_ma60_pct: float | None
    # bullish = MA5 > MA10 > MA20 (多頭排列), bearish = the reverse, mixed = neither.
    alignment: Literal["bullish", "bearish", "mixed", "unknown"]


class VolumeFeatures(BaseModel):
    latest_shares: int | None
    avg5_shares: int | None
    avg20_shares: int | None
    # Ratios rather than differences: "1.8x the 5-day average" is scale free,
    # so the same prompt reads the same way for 台積電 and for a 30-dollar stock.
    ratio_to_avg5: float | None
    ratio_to_avg20: float | None
    trend: Literal["expanding", "contracting", "steady", "unknown"]


class MomentumFeatures(BaseModel):
    return_1d_pct: float | None
    return_5d_pct: float | None
    return_20d_pct: float | None
    return_60d_pct: float | None
    # Signed: positive = that many consecutive up days, negative = down days.
    consecutive_days: int
    gap_pct: float | None


class RangeFeatures(BaseModel):
    """Where the close sits inside the window -- 位階.

    `position_pct` is 0 at the window low and 100 at its high. Entry size is a
    question about how much room is left, and a bare close cannot answer it.
    """

    window_days: int
    high: float | None
    low: float | None
    position_pct: float | None
    drawdown_from_high_pct: float | None


class VolatilityFeatures(BaseModel):
    # Standard deviation of daily returns over the window, in percent.
    stdev_20d_pct: float | None
    # Mean absolute daily move, a plainer statistic for a prompt to reason about.
    avg_abs_move_20d_pct: float | None


class PriceFeatures(BaseModel):
    """Everything the AI engine is allowed to see, derived from stored bars only.

    Deliberately a *closed* structure rather than raw OHLCV: the prompt is built
    from these fields, so what the model reasons about is reviewable, diffable
    and reproducible. Handing it 60 rows of numbers would make every answer
    depend on the model's own arithmetic, which is the part it is worst at.
    """

    as_of: datetime.date
    sample_size: int
    latest_close: float
    ma: MaFeatures
    volume: VolumeFeatures
    momentum: MomentumFeatures
    range: RangeFeatures
    volatility: VolatilityFeatures
    # The last five 3-day-vs-6-day MA bias readings: the same series the 四大買賣點
    # gate pivots on, so the model can be asked to agree or disagree with it.
    bias_3_6: list[float]


#: What to do. `hold` is a first-class answer, not a fallback -- see AiVerdict.
AiAction = Literal["enter", "exit", "hold"]
#: How much of a position the action applies to. Null exactly when action is hold.
AiSize = Literal["large", "medium", "small"]


class AiVerdict(BaseModel):
    """The position call.

    `size` is null if and only if `action` is "hold". Keeping direction and
    magnitude in separate fields (rather than one seven-valued enum) is what
    lets the UI render 進場/退場 and 大/中/小 independently, and lets a backtest
    score direction without having to agree about sizing.

    A model asked for a recommendation will nearly always produce one. twstock's
    broken 四大買賣點 never returned Don't touch in 20 000 draws and that was a
    bug; an AI engine that never says hold has the same defect, so the prompt
    names hold as a valid answer and the schema keeps it cheap to express.
    """

    action: AiAction
    size: AiSize | None
    confidence: Literal["high", "medium", "low"]
    # One sentence, shown on the card before anything is expanded.
    headline: str
    reasons: list[str]
    risks: list[str]


class AiAnalysisResponse(BaseModel):
    sid: str
    name: str
    # The trading day the bars end on -- what the verdict is *about*, and the
    # cache key. `generated_at` is when the model was asked, which differs after
    # a weekend and is what the card timestamps.
    as_of: datetime.date
    generated_at: datetime.datetime
    model: str
    prompt_version: str
    locale: str
    #: False only when this call actually spent a Gemini request.
    cached: bool
    verdict: AiVerdict
    features: PriceFeatures
    # The rule engine's answer for the same bars, so the card can put the
    # deterministic and the generated verdict side by side -- the comparison the
    # project was built to make.
    traditional: BestFourPointResult


class AiQuotaStatus(BaseModel):
    """What is left of the caller's daily generation allowance."""

    used: int
    limit: int
    resets_at: datetime.datetime


# --- Hold analysis (存股) -----------------------------------------------------
#
# A third question about the same stock, and the reason it needs its own
# vocabulary rather than another field on the two above: those ask what to do
# with a position over days, this asks whether the company is worth accumulating
# and holding for its dividend over years. Nothing about enter/exit/size
# survives the change of horizon, and grading the two on one scale would be the
# blended score this lane exists to avoid.


class HoldDividendFeatures(BaseModel):
    """What cash the company has actually paid, and what that is worth today.

    `coverage` is repeated from the dividend card because it decides how much
    of this can be believed: TWSE publishes a yearly archive, TPEX only the
    current window, so `consecutive_years_with_cash` from a `recent` source is
    a floor rather than a streak.
    """

    coverage: Literal["history", "recent", "none"]
    window_years: int
    #: Calendar years inside the window this database has actually pulled the
    #: archive for. Distinct from `coverage`, which says what the exchange
    #: publishes: on a fresh database TWSE coverage is still "history" while
    #: only the handful of years the dividend card asked for are here. A
    #: continuity rule that ignored this would fail a company for a gap that is
    #: ours rather than theirs.
    years_observed: int
    #: Distinct calendar years inside the window with at least one cash payment.
    years_with_cash: int
    #: Unbroken run of such years, counted back from the last *complete* year.
    #: The current year is excluded: a company that pays in August is not a
    #: company that stopped paying, when read in March.
    consecutive_years_with_cash: int
    ttm_cash: float | None
    cash_yield_pct: float | None
    #: Mean cash per year across the window, and its yield -- what a holder
    #: collects on average rather than in the last twelve months alone.
    avg_cash_per_year: float | None
    avg_yield_pct: float | None
    latest_ex_date: datetime.date | None
    #: Years that paid stock rather than (or as well as) cash. Dilution the
    #: cash yield above does not show.
    years_with_stock_dividend: int


class HoldLiquidityFeatures(BaseModel):
    """Whether a position can actually be built by buying a little at a time."""

    trading_days: int
    avg_daily_shares: int | None
    avg_daily_turnover: int | None
    #: Sessions inside the window that traded nothing at all. A thin name can
    #: pass an average and still be impossible to accumulate.
    no_trade_days: int


class HoldFundamentalsFeatures(BaseModel):
    """Annual EPS and ROE, and the valuation drawn from them.

    Every field is nullable and `years_available` is the one to read first:
    zero means `fundamentals_annual` has nothing for this sid, which is the
    normal state until the ingest lands, and the Earn / Efficient / Cheap
    dimensions report `unknown` rather than being scored from nothing.
    """

    years_available: int
    #: Years inside the checklist window that have an EPS figure at all.
    eps_years_checked: int
    eps_positive_years: int | None
    latest_eps: float | None
    latest_eps_year: int | None
    avg_eps: float | None
    avg_roe_pct: float | None
    #: Spread of ROE across the window. Chen's "efficient" is a return that
    #: keeps happening, so a high average built from one spike is not it.
    roe_stdev_pct: float | None
    roe_years_checked: int
    #: Latest close divided by the most recent annual EPS. Trailing and annual,
    #: so it lags a turnaround by up to a year -- which is the conservative
    #: direction for a checklist about not overpaying.
    trailing_pe: float | None


class HoldPriceFeatures(BaseModel):
    """Where the price sits in its own multi-year range.

    Not a signal. "Buy a good company when it is cheap" needs some notion of
    cheap that does not depend on earnings data we may not have, and position
    inside the long range is the one the bars alone can supply.
    """

    latest_close: float | None
    window_high: float | None
    window_low: float | None
    position_pct: float | None
    drawdown_from_high_pct: float | None
    return_1y_pct: float | None


class HoldFeatures(BaseModel):
    """Everything the 存股 lane reasons from, derived from stored rows only.

    Same contract as `PriceFeatures`: a closed structure, the arithmetic done
    here rather than in a prompt, and no clock read inside the extractor -- so
    a past date is replayed by slicing the inputs and nothing else.
    """

    sid: str
    name: str
    #: Trading day the price rows end on. None when there are no usable bars,
    #: which is also the one case with no cache key to store a verdict under.
    as_of: datetime.date | None
    #: 產業別 from the ISIN listing. Carried because the valuation band differs
    #: by sector -- a bank at 10x and a manufacturer at 10x are not the same
    #: statement -- and because peer comparison will key on it later.
    industry: str
    dividend: HoldDividendFeatures
    liquidity: HoldLiquidityFeatures
    fundamentals: HoldFundamentalsFeatures
    price: HoldPriceFeatures


#: The five things the checklist asks. Named so the UI can render its own copy
#: for each rather than displaying a server-composed sentence.
ChenDimensionKey = Literal["earn", "efficient", "cheap", "collect", "liquid"]
#: `unknown` is a first-class outcome: it shrinks the denominator instead of
#: counting as a failure, so a company we have no EPS for is not scored as one
#: that lost money.
ChenStatus = Literal["pass", "fail", "unknown"]
HoldSuitability = Literal["strong", "ok", "weak", "avoid"]


class ChenDimension(BaseModel):
    key: ChenDimensionKey
    status: ChenStatus
    #: Share of the checklist this dimension carries. Only known dimensions
    #: contribute to either side of the score.
    weight: int
    #: English, and not shown to the user. It goes into the prompt and into an
    #: operator's log; the card composes its own sentence from `metrics` so the
    #: copy stays in the frontend catalogue where both locales can see it.
    evidence: str
    #: The numbers behind the verdict, for the UI to format and for a reader to
    #: check the rule against. Values that could not be computed are omitted.
    metrics: dict[str, float]


class ChenRuleResult(BaseModel):
    """The deterministic checklist. No model, no network, no cost.

    `score` is out of 100 over the *known* dimensions only, so a thinly covered
    name is not punished for the data we are missing -- but it is also not
    comparable to a fully covered one beyond what `known_weight` supports,
    which is why that number is in the response rather than left implicit.
    """

    score: int | None
    suitability: HoldSuitability | None
    dimensions: list[ChenDimension]
    #: Weight of the dimensions that could be scored, out of `total_weight`.
    known_weight: int
    total_weight: int
    #: Machine-readable reasons a dimension came back unknown, e.g.
    #: "no_annual_fundamentals". The UI renders each from its own catalogue.
    coverage_gaps: list[str]


class ChenAnalysisResponse(BaseModel):
    sid: str
    name: str
    as_of: datetime.date | None
    features: HoldFeatures
    rules: ChenRuleResult


class AiHoldVerdict(BaseModel):
    """The 存股 call.

    One axis, not two. A holding decision has no equivalent of position size:
    the method's answer to "how much" is "regularly, over years", which is the
    same for every name it approves of.
    """

    suitability: HoldSuitability
    confidence: Literal["high", "medium", "low"]
    headline: str
    reasons: list[str]
    risks: list[str]
    #: Whether the model landed on the same suitability as the checklist. Null
    #: when the checklist could score nothing, so there was no verdict to agree
    #: with.
    agrees_with_rules: bool | None


class AiHoldAnalysisResponse(BaseModel):
    sid: str
    name: str
    as_of: datetime.date
    generated_at: datetime.datetime
    model: str
    prompt_version: str
    locale: str
    #: False only when this call actually spent a Gemini request.
    cached: bool
    verdict: AiHoldVerdict
    features: HoldFeatures
    #: The checklist's answer for the same snapshot, so the card can show the
    #: deterministic and the generated verdict side by side.
    rules: ChenRuleResult


class BacktestHorizonStats(BaseModel):
    """How one signal type scored over one forward horizon."""

    horizon: int
    samples: int
    wins: int
    #: Signals too recent to have run the horizon out. Excluded from `samples`,
    #: so `win_rate` is never a partial outcome scored as a final one.
    pending: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None


class BacktestBaselineStats(BaseModel):
    """The same horizon over every judged day, signal or not.

    Read `BacktestHorizonStats.win_rate` against this and never against 50 %.
    A rule that is right 55 % of the time, in a window where 58 % of all days
    closed higher, lost to doing nothing -- and the win rate alone cannot say
    so, which is why this ships in the same response rather than as something
    the caller is trusted to look up.
    """

    horizon: int
    samples: int
    ups: int
    up_rate: float | None
    average_return: float | None
    median_return: float | None


class BacktestEdge(BaseModel):
    """Signal minus baseline. Zero or below means the rule added nothing.

    Served precomputed because the sell side is not the arithmetic a caller
    guesses: a Sell competes with the days that *fell* (`1 - up_rate`), and its
    excess return is the drop it avoided (baseline minus signal).
    """

    horizon: int
    buy_edge: float | None
    sell_edge: float | None
    buy_excess_return: float | None
    sell_excess_return: float | None


class BacktestSignalOut(BaseModel):
    """One historical verdict and what the price did after it.

    `forward` is keyed by horizon in trading days; a key is absent when the
    window ended before that horizon did. Absent means unknown, not zero.
    """

    date: datetime.date
    signal: Literal["buy", "sell"]
    close: float
    reasons: list[str]
    forward: dict[int, float]


class BacktestTradeOut(BaseModel):
    entry_date: datetime.date
    entry_price: float
    exit_date: datetime.date
    exit_price: float
    holding_days: int
    profit: float


class BacktestEquityPoint(BaseModel):
    """Both curves on one point, indexed to 1.0 at the first judged bar.

    Carried on shared points rather than as two series so the chart cannot draw
    them over different date ranges, which is the one way an equity comparison
    lies without looking wrong.
    """

    date: datetime.date
    strategy: float
    buy_hold: float


class BacktestSimulationOut(BaseModel):
    """Long-only replay: in on a Buy, out on a Sell, filled at the next open.

    `strategy_return` includes any position still open at the end, marked to
    the last close; `open_entry_date` is how a caller can tell. That position
    is deliberately not one of `trades`, so it never reaches the trade stats.
    """

    trades: list[BacktestTradeOut]
    trade_count: int
    winning_trades: int
    strategy_return: float
    buy_hold_return: float
    max_drawdown: float
    buy_hold_max_drawdown: float
    open_entry_date: datetime.date | None
    open_entry_price: float | None
    #: Fraction of judged days spent holding. A rule 80 % in cash can only ever
    #: capture a fifth of a rally, however good its hit rate looks.
    exposure: float
    #: Null rather than 0 when nothing closed: "never won" and "never traded"
    #: are different answers and must not render the same.
    trade_win_rate: float | None
    average_holding_days: float | None
    #: Daily mark-to-market of both curves. Carried only by the single-stock
    #: response, which draws it; the batch reports rates and would ship a
    #: megabyte of points to plot nothing.
    equity: list[BacktestEquityPoint] = []


class BacktestResponse(BaseModel):
    """One stock's replay in full, served from `backtest_result`.

    The batch sibling below answers the same question across a basket and
    stops at the rates. This one carries the equity curve, the trade list and
    the recent signals, because it backs a card someone is reading about one
    company.

    `cached` says whether this came out of the table untouched or was
    recomputed on the spot because the bars had moved past the stored row.
    Surfaced rather than hidden: a board that quietly serves last week's answer
    to "is this signal working" is worse than one that admits it is catching up.
    """

    sid: str
    name: str
    rule_set: Literal["grs", "twstock"]
    #: The trailing window replayed, in months. Fixed by configuration rather
    #: than chosen per request -- see `BacktestResult` for why.
    window_months: int
    start: datetime.date
    end: datetime.date
    bars: int
    judged_days: int
    signal_count: int
    buy_stats: list[BacktestHorizonStats]
    sell_stats: list[BacktestHorizonStats]
    baseline: list[BacktestBaselineStats]
    edges: list[BacktestEdge]
    simulation: BacktestSimulationOut
    signals: list[BacktestSignalOut]  # most recent first, capped

    computed_at: datetime.datetime
    computed_through: datetime.date
    cached: bool


class BacktestSummary(BaseModel):
    """One stock's scorecard: the rates, without the per-signal list.

    Everything is null and `note` explains why when the cache held too few
    bars. A stock that could not be scored stays in `items` rather than
    vanishing -- otherwise `pooled` reads as covering the whole basket when it
    covered part of it.
    """

    sid: str
    name: str
    rule_set: Literal["grs", "twstock"]
    start: datetime.date | None
    end: datetime.date | None
    bars: int
    judged_days: int
    signal_count: int
    buy_stats: list[BacktestHorizonStats]
    sell_stats: list[BacktestHorizonStats]
    baseline: list[BacktestBaselineStats]
    edges: list[BacktestEdge]
    strategy_return: float | None
    buy_hold_return: float | None
    #: Fraction of judged days spent holding. A rule 80 % in cash can only ever
    #: capture a fifth of a rally, however good its hit rate looks.
    exposure: float | None
    trade_count: int
    note: str | None


class BacktestPooledHorizon(BaseModel):
    """The basket summed per horizon -- the only readable number here.

    A single stock's year fires a handful of signals, and a rate off a handful
    swings twenty points on one trade. `stocks` and the `*_samples` counts are
    part of the answer rather than decoration: a pooled rate over 30 signals is
    still noise, and the caller needs to be able to see that.
    """

    horizon: int
    stocks: int
    buy_samples: int
    buy_win_rate: float | None
    buy_average_return: float | None
    sell_samples: int
    sell_win_rate: float | None
    sell_average_return: float | None
    baseline_samples: int
    baseline_up_rate: float | None
    baseline_average_return: float | None
    buy_edge: float | None
    sell_edge: float | None
    buy_excess_return: float | None
    sell_excess_return: float | None


class BacktestBatchResponse(BaseModel):
    items: list[BacktestSummary]
    #: Pooled over the stocks in `items` that had enough bars to score. Empty
    #: when none did.
    pooled: list[BacktestPooledHorizon]
    #: Codes that could not be resolved at all. Same contract as the
    #: traditional batch: one bad sid does not drop the rest.
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


class JobHealth(BaseModel):
    """Which background jobs are currently in a failed state."""

    # Judged on each job's most recent attempt only: a failure that the retry
    # already fixed is history, not a fault.
    failing: list[str] = []
    last_failure_at: datetime.datetime | None = None


class BackupHealth(BaseModel):
    """Age of the newest database dump on disk.

    "unchecked" is not a fault: it means BACKUP_STATUS_DIR is unset, which is
    the normal state for a server running outside Docker.
    """

    status: Literal["ok", "stale", "missing", "unchecked"]
    taken_at: datetime.datetime | None = None
    age_hours: float | None = None


class HealthResponse(BaseModel):
    # The rollup an uptime monitor should watch. "degraded" covers an
    # unreachable database *and* the quieter faults below -- a failing nightly
    # job or a backup that stopped happening are exactly the things nobody
    # notices for a fortnight, so they have to move this field.
    status: Literal["ok", "degraded"]
    database: str
    stock_codes_loaded: int
    # None until `stock_code` has been reconciled with the exchanges at least
    # once -- i.e. the listing on offer is still twstock's bundled snapshot.
    stock_codes_synced_at: datetime.datetime | None = None

    jobs: JobHealth = JobHealth()
    backup: BackupHealth = BackupHealth(status="unchecked")
    # One human-readable line per reason `status` is not "ok"; empty when it
    # is. A monitor that can only match on text has something to match on, and
    # whoever reads the alert has the reason in the alert body.
    alerts: list[str] = []


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
    # True between self-service registration and an ADMIN activating the
    # account. Distinguishes "waiting to be let in" from "was let in and then
    # suspended" -- both of which are is_active=False.
    pending_approval: bool
    # Set while the account is locked out after repeated failed sign-ins.
    # Exposed so an admin can see why somebody cannot get in without reading
    # the server log, and so the UI can offer to lift it.
    locked_until: datetime.datetime | None
    created_at: datetime.datetime


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    phone: str | None = None


class RegistrationPolicy(BaseModel):
    """What self-service registration currently does. Public.

    The Register page has to know before it renders: under review, submitting
    the form does not sign you in, and telling the user that only after they
    have typed everything in is how you get a bug report about a broken signup.
    """

    open: bool  # False once the deployment stops accepting new accounts at all
    requires_approval: bool


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


class RegisterResponse(BaseModel):
    """The outcome of POST /api/auth/register, in one of two shapes.

    `tokens` is present exactly when `pending` is false. Under review there is
    deliberately nothing to hand back: the account exists but may not be used,
    and issuing a token that every other route answers 403 to would only make
    the client look signed in while nothing worked.
    """

    pending: bool
    user: UserOut
    tokens: TokenResponse | None = None


class ProfileUpdateRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class UserListResponse(BaseModel):
    total: int
    # How many accounts are waiting for approval *in total*, not in this page
    # and not after the filter -- it is the badge the admin console shows.
    pending_total: int = 0
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


class WatchlistGroupOut(BaseModel):
    id: int
    name: str
    position: int


class WatchlistResponse(BaseModel):
    count: int
    sids: list[str]
    groups: list[WatchlistGroupOut] = []
    # Only the assigned sids; missing means ungrouped. Keys are stock codes.
    group_by_sid: dict[str, int] = {}


class WatchlistUpdateRequest(BaseModel):
    sids: list[str]
    # Overlay on the assignments that survive the replace. None (or omitted)
    # leaves every remaining sid in the group it was already in; a null value
    # ungroups that sid. Unknown group ids are rejected, not silently dropped.
    group_by_sid: dict[str, int | None] | None = None


class WatchlistGroupCreateRequest(BaseModel):
    name: str


class WatchlistGroupUpdateRequest(BaseModel):
    name: str
