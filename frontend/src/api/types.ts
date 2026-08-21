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
  users: User[]
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

export interface WatchlistResponse {
  count: number
  sids: string[]
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
