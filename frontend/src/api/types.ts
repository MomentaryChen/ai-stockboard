export type DataSource = 'twse' | 'tpex'

/** 四大買賣點規則版本. 'grs' 是修正過的參考行為，'twstock' 是套件原樣（供對照）。 */
export type RuleSet = 'grs' | 'twstock'

export interface StockInfo {
  code: string
  name: string
  type: string
  market: string
  group: string
  isin: string
  start: string
  data_source: DataSource
  /** false once the exchange stops listing the code; it stays chartable. */
  is_active: boolean
}

export interface SearchResponse {
  query: string
  total: number
  results: StockInfo[]
}

export interface DailyPricePoint {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  change: number | null
  capacity: number | null
  turnover: number | null
  transaction: number | null
}

export interface HistoryResponse {
  sid: string
  name: string
  source: DataSource
  months: number
  count: number
  fetched_months: string[]
  cached_months: string[]
  data: DailyPricePoint[]
}

export interface DividendEvent {
  ex_date: string
  kind: string
  cash_dividend: number | null
  stock_dividend: number | null
  deduction: number | null
  close_before: number | null
  reference_price: number | null
  upcoming: boolean
}

export interface DividendResponse {
  sid: string
  name: string
  source: DataSource
  coverage: 'history' | 'recent' | 'none'
  years: number
  count: number
  ttm_cash: number | null
  latest_close: number | null
  yield_percent: number | null
  events: DividendEvent[]
}

/** Consecutive same-sign institutional days, counted back from the newest row. */
export interface ChipFlow {
  streak: 'buy' | 'sell' | 'none'
  streak_days: number
  /** Sum of up to 5 recent sessions, in 股. */
  net_5d: number | null
}

export interface ChipDay {
  date: string
  foreign_net: number | null
  trust_net: number | null
  dealer_net: number | null
  total_net: number | null
  margin_balance: number | null
  margin_change: number | null
  short_balance: number | null
  short_change: number | null
}

export interface ChipResponse {
  sid: string
  name: string
  source: DataSource
  /** daily = the all-market reports; none = indices, which have no per-name chip. */
  coverage: 'daily' | 'none'
  days: number
  as_of: string | null
  count: number
  fetched_dates: string[]
  cached_dates: string[]
  foreign: ChipFlow
  trust: ChipFlow
  dealer: ChipFlow
  total: ChipFlow
  margin_balance: number | null
  margin_change: number | null
  short_balance: number | null
  short_change: number | null
  rows: ChipDay[]
}

export interface MovingAverages {
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
}

export interface MaSeriesPoint extends MovingAverages {
  date: string
}

export interface BestFourPointResult {
  signal: 'buy' | 'sell' | 'hold'
  label: string
  reasons: string[]
}

export interface TraditionalAnalysisResponse {
  sid: string
  name: string
  rule_set: RuleSet
  as_of: string
  sample_size: number
  latest_close: number | null
  moving_averages: MovingAverages
  ma_series: MaSeriesPoint[]
  best_four_point: BestFourPointResult
}

export interface TraditionalAnalysisSummary {
  sid: string
  name: string
  rule_set: RuleSet
  as_of: string | null
  sample_size: number
  latest_close: number | null
  best_four_point: BestFourPointResult
}

export interface TraditionalAnalysisBatchResponse {
  items: TraditionalAnalysisSummary[]
  errors: Record<string, string>
}

// --- AI analysis ---
//
// The rule engine answers buy/sell/hold. The AI engine answers a position
// question, so it has its own vocabulary: what to do, and with how much.

/** `size` is null exactly when the action is `hold`. */
export type AiAction = 'enter' | 'exit' | 'hold'
export type AiSize = 'large' | 'medium' | 'small'

export interface AiVerdict {
  action: AiAction
  size: AiSize | null
  confidence: 'high' | 'medium' | 'low'
  headline: string
  reasons: string[]
  risks: string[]
}

export interface AiMaFeatures {
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  close_vs_ma5_pct: number | null
  close_vs_ma20_pct: number | null
  close_vs_ma60_pct: number | null
  alignment: 'bullish' | 'bearish' | 'mixed' | 'unknown'
}

export interface AiVolumeFeatures {
  latest_shares: number | null
  avg5_shares: number | null
  avg20_shares: number | null
  ratio_to_avg5: number | null
  ratio_to_avg20: number | null
  trend: 'expanding' | 'contracting' | 'steady' | 'unknown'
}

export interface AiMomentumFeatures {
  return_1d_pct: number | null
  return_5d_pct: number | null
  return_20d_pct: number | null
  return_60d_pct: number | null
  consecutive_days: number
  gap_pct: number | null
}

export interface AiRangeFeatures {
  window_days: number
  high: number | null
  low: number | null
  position_pct: number | null
  drawdown_from_high_pct: number | null
}

export interface AiVolatilityFeatures {
  stdev_20d_pct: number | null
  avg_abs_move_20d_pct: number | null
}

/** Exactly what the model was shown -- rendered so a verdict can be checked. */
export interface AiPriceFeatures {
  as_of: string
  sample_size: number
  latest_close: number
  ma: AiMaFeatures
  volume: AiVolumeFeatures
  momentum: AiMomentumFeatures
  range: AiRangeFeatures
  volatility: AiVolatilityFeatures
  bias_3_6: number[]
}

export interface AiAnalysisResponse {
  sid: string
  name: string
  as_of: string
  generated_at: string
  model: string
  prompt_version: string
  locale: string
  /** False only when this call actually spent a Gemini request. */
  cached: boolean
  verdict: AiVerdict
  features: AiPriceFeatures
  /** The rule engine's answer for the same bars, for the side-by-side. */
  traditional: BestFourPointResult
}

export interface AiQuotaStatus {
  used: number
  limit: number
  resets_at: string
}

/** Live Gemini model and the env allowlist the admin picker may choose from. */
export interface AiModelSettings {
  model: string
  available_models: string[]
  updated_at: string | null
  updated_by: string | null
}

/** How a signal type scored over one forward horizon.
 *
 *  `samples` counts only signals whose horizon has fully elapsed; `pending` is
 *  the rest. Every derived field is null rather than 0 when there is nothing to
 *  derive it from -- "0% of the time" and "no signals yet" are opposite
 *  messages and must not render the same. */
export interface BacktestHorizonStats {
  horizon: number
  samples: number
  wins: number
  pending: number
  win_rate: number | null
  average_return: number | null
  median_return: number | null
}

/** The same horizon over every judged day, signal or not.
 *
 *  Read `BacktestHorizonStats.win_rate` against this and never against 50%. */
export interface BacktestBaselineStats {
  horizon: number
  samples: number
  ups: number
  up_rate: number | null
  average_return: number | null
  median_return: number | null
}

/** Signal minus baseline. At or below zero, the rule added nothing.
 *
 *  Precomputed by the server because the sell side's arithmetic is not what
 *  anyone guesses: a Sell competes with the days that *fell*. */
export interface BacktestEdge {
  horizon: number
  buy_edge: number | null
  sell_edge: number | null
  buy_excess_return: number | null
  sell_excess_return: number | null
}

export interface BacktestSignal {
  date: string
  signal: 'buy' | 'sell'
  close: number
  reasons: string[]
  /** Keyed by horizon in trading days. A key is absent when the window ended
   *  before that horizon did -- absent means unknown, not zero. */
  forward: Record<string, number>
}

export interface BacktestTrade {
  entry_date: string
  entry_price: number
  exit_date: string
  exit_price: number
  holding_days: number
  profit: number
}

export interface BacktestEquityPoint {
  date: string
  strategy: number
  buy_hold: number
}

export interface BacktestSimulation {
  trades: BacktestTrade[]
  trade_count: number
  winning_trades: number
  strategy_return: number
  buy_hold_return: number
  max_drawdown: number
  buy_hold_max_drawdown: number
  open_entry_date: string | null
  open_entry_price: number | null
  /** Fraction of judged days spent holding. A rule 80% in cash can only ever
   *  capture 20% of a rally, however good its hit rate looks. */
  exposure: number
  trade_win_rate: number | null
  average_holding_days: number | null
  equity: BacktestEquityPoint[]
}

export interface BacktestResponse {
  sid: string
  name: string
  rule_set: RuleSet
  window_months: number
  start: string
  end: string
  bars: number
  judged_days: number
  signal_count: number
  buy_stats: BacktestHorizonStats[]
  sell_stats: BacktestHorizonStats[]
  baseline: BacktestBaselineStats[]
  edges: BacktestEdge[]
  simulation: BacktestSimulation
  signals: BacktestSignal[]
  computed_at: string
  computed_through: string
  cached: boolean
}

export interface BacktestSummary {
  sid: string
  name: string
  rule_set: RuleSet
  start: string | null
  end: string | null
  bars: number
  judged_days: number
  signal_count: number
  buy_stats: BacktestHorizonStats[]
  sell_stats: BacktestHorizonStats[]
  baseline: BacktestBaselineStats[]
  edges: BacktestEdge[]
  strategy_return: number | null
  buy_hold_return: number | null
  exposure: number | null
  trade_count: number
  /** Why this stock could not be scored. Non-null means every figure is null. */
  note: string | null
}

export interface BacktestPooledHorizon {
  horizon: number
  stocks: number
  buy_samples: number
  buy_win_rate: number | null
  buy_average_return: number | null
  sell_samples: number
  sell_win_rate: number | null
  sell_average_return: number | null
  baseline_samples: number
  baseline_up_rate: number | null
  baseline_average_return: number | null
  buy_edge: number | null
  sell_edge: number | null
  buy_excess_return: number | null
  sell_excess_return: number | null
}

export interface BacktestBatchResponse {
  items: BacktestSummary[]
  pooled: BacktestPooledHorizon[]
  errors: Record<string, string>
}

export interface RealtimeQuote {
  code: string
  name: string
  fullname: string
  time: string
  timestamp: number
  open: number | null
  high: number | null
  low: number | null
  latest_trade_price: number | null
  trade_volume: number | null
  accumulate_trade_volume: number | null
  yesterday_close: number | null
  change: number | null
  change_percent: number | null
  best_bid_price: number[]
  best_bid_volume: number[]
  best_ask_price: number[]
  best_ask_volume: number[]
}

export interface RealtimeResponse {
  success: boolean
  message: string | null
  quotes: RealtimeQuote[]
  errors: Record<string, string>
}

/** Which way a move went, once "barely moved" has been rounded off to flat. */
export type MoveDirection = 'up' | 'down' | 'flat'

/** One instrument's opening picture for one trading day.
 *
 *  `last` is the day's close for a settled session and the current price for an
 *  intraday one, so everything derived from it follows suit -- which is what
 *  `intraday` is for. The server only ever produces the settled variant; the
 *  intraday one is assembled on the client from a realtime quote, because
 *  today's daily bar does not exist until TWSE publishes the day's report.
 */
export interface OpenSnapshot {
  sid: string
  name: string
  date: string
  is_index: boolean
  intraday: boolean

  open: number | null
  prev_close: number | null
  /** 跳空: open - prev_close, the overnight repricing. */
  gap: number | null
  gap_percent: number | null
  gap_direction: MoveDirection

  high: number | null
  low: number | null
  last: number | null
  change: number | null
  change_percent: number | null

  /** last - open: what the session itself did, which the close alone hides. */
  from_open: number | null
  from_open_percent: number | null
  drift_direction: MoveDirection

  capacity: number | null
  turnover: number | null
}

export interface MarketOpenResponse {
  date: string
  is_today: boolean
  /** True once the index's bar for `date` exists -- i.e. the numbers are final. */
  settled: boolean
  latest_trading_day: string | null
  items: OpenSnapshot[]
  errors: Record<string, string>
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  database: string
  stock_codes_loaded: number
  /** null while the listing is still twstock's bundled snapshot. */
  stock_codes_synced_at: string | null
}

// ---------------------------------------------------------------------------
// Accounts, tokens and watchlists
// ---------------------------------------------------------------------------

export type Role = 'ADMIN' | 'USER'

export interface User {
  id: number
  username: string
  email: string
  phone: string | null
  role: Role
  is_active: boolean
  /**
   * Set between an ADMIN password reset and the user choosing their own
   * password. While true the API allows only GET /api/auth/me and
   * POST /api/auth/me/password -- everything else answers 403 -- and the UI
   * holds them on /change-password.
   */
  must_change_password: boolean
  /**
   * True between self-service registration and an ADMIN activating the
   * account. Together with `is_active` it separates the two dormant states
   * the admin console has to word differently: waiting to be let in
   * (`is_active` false, this true) and let in then suspended (both false).
   */
  pending_approval: boolean
  /**
   * ISO timestamp the account stops being locked out after repeated failed
   * sign-ins, or null when it is not locked. Only ever set by the server.
   */
  locked_until: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  /** Seconds the access token stays valid. */
  expires_in: number
  user: User
}

export interface UserListResponse {
  total: number
  /** Accounts awaiting approval across the whole table, not just this page --
   *  it is a badge, so a filtered or paginated count would be misleading. */
  pending_total: number
  users: User[]
}

/** What signing up currently does, read before the Register form renders. */
export interface RegistrationPolicy {
  open: boolean
  requires_approval: boolean
}

/** POST /api/auth/register, in one of two shapes.
 *
 *  `tokens` is present exactly when `pending` is false. Under review the
 *  server deliberately issues nothing: the account exists and may not be used
 *  yet, so there is no session to adopt.
 */
export interface RegisterResponse {
  pending: boolean
  user: User
  tokens: TokenResponse | null
}

/** The one response in the API that carries a secret. */
export interface PasswordResetResponse {
  user: User
  /**
   * The generated password, in the clear and exactly once: the server stores
   * only its hash and cannot produce it again. Show it, let the admin copy it,
   * and never persist it anywhere.
   */
  temp_password: string
}

export interface WatchlistGroup {
  id: number
  name: string
  position: number
}

export interface WatchlistResponse {
  count: number
  sids: string[]
  groups: WatchlistGroup[]
  /** Assigned sids only; a missing key means ungrouped. */
  group_by_sid: Record<string, number>
}

/** --- 背景排程作業（ADMIN） --- */

/** `skipped` 代表工作醒來後確認沒事可做 —— 那是排程還活著的心跳，不是失敗。 */
export type JobStatus = 'success' | 'skipped' | 'failed'
export type JobTrigger = 'startup' | 'schedule' | 'manual'
/** `interval` 每 N 分鐘一次；`daily` 每天固定時刻。 */
export type ScheduleKind = 'interval' | 'daily'

export interface JobRun {
  id: number
  job_id: string
  started_at: string
  finished_at: string
  duration_seconds: number
  status: JobStatus
  trigger: JobTrigger
  /** 按下手動執行的管理員帳號；排程執行為 null。 */
  actor: string | null
  /** 各工作自己的計數，key 由 `Job.stat_labels` 定義。 */
  stats: Record<string, number>
  message: string | null
}

export interface JobSchedule {
  enabled: boolean
  kind: ScheduleKind
  interval_minutes: number
  /** 'HH:MM'，以 `timezone` 為準。 */
  daily_at: string
  timezone: string
  /** 後端的護欄；表單照著限制輸入，但真正把關的是伺服器。 */
  min_interval_minutes: number
  max_interval_minutes: number
  /** true 代表還沒有人改過，跑的是環境變數給的預設值。 */
  is_default: boolean
  updated_at: string | null
  updated_by: string | null
}

export interface Job {
  id: string
  name: string
  description: string
  schedule: JobSchedule
  running: boolean
  expected_seconds: number
  manual_cooldown_seconds: number
  /** stats 的 key → 表格欄位標題。一張表就能顯示所有工作。 */
  stat_labels: Record<string, string>
  last_run: JobRun | null
  /** 最後一次真的做了事的執行；只有心跳不算。 */
  last_success_at: string | null
  next_run_at: string | null
  total_runs: number
}

export interface JobListResponse {
  /** false 代表整個排程器被關掉，所有工作都只能手動執行。 */
  scheduler_enabled: boolean
  timezone: string
  jobs: Job[]
}

export interface JobRunsResponse {
  job: Job
  total: number
  runs: JobRun[]
}

export interface JobScheduleUpdate {
  enabled?: boolean
  kind?: ScheduleKind
  interval_minutes?: number
  daily_at?: string
}

export interface JobTriggerResponse {
  job_id: string
  started: boolean
  message: string
}
