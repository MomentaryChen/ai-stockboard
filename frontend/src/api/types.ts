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

export interface HealthResponse {
  status: 'ok' | 'degraded'
  database: string
  stock_codes_loaded: number
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

export interface WatchlistResponse {
  count: number
  sids: string[]
}

/** --- 上市櫃名冊同步（ADMIN） --- */

export type SyncStatus = 'synced' | 'skipped' | 'failed'
export type SyncTrigger = 'startup' | 'schedule' | 'manual'

export interface CodeSyncResponse {
  status: SyncStatus
  synced_at: string | null
  active: number
  inserted: number
  updated: number
  delisted: number
  pruned: number
  message: string | null
}

export interface SyncRun {
  id: number
  started_at: string
  finished_at: string
  duration_seconds: number
  status: SyncStatus
  trigger: SyncTrigger
  /** markets that answered: 'twse' / 'tpex'. One of two means a partial run. */
  sources: string[]
  active: number
  inserted: number
  updated: number
  delisted: number
  pruned: number
  message: string | null
}

export interface SyncRunsResponse {
  enabled: boolean
  interval_hours: number
  last_success_at: string | null
  /** null while the listing is still twstock's bundled snapshot. */
  synced_at: string | null
  active: number
  total: number
  runs: SyncRun[]
}
