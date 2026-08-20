export type DataSource = 'twse' | 'tpex'

export interface StockInfo {
  code: string
  name: string
  type: string
  market: string
  group: string
  isin: string
  start: string
  data_source: DataSource
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
