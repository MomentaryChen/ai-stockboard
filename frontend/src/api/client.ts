import type {
  RuleSet,
  TraditionalAnalysisResponse,
  HealthResponse,
  HistoryResponse,
  RealtimeResponse,
  SearchResponse,
  StockInfo,
} from './types'

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* response was not JSON -- keep the status text */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

/** 台股大盤（發行量加權股價指數）. The API exposes it as an ordinary sid, so
 *  history / analysis / realtime all take the same routes a stock does. */
export const MARKET_INDEX_SID = 't00'

export const api = {
  health: () => request<HealthResponse>('/api/health'),

  searchStocks: (q: string, limit = 20, signal?: AbortSignal) =>
    request<SearchResponse>(
      `/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      signal,
    ),

  getStock: (sid: string) => request<StockInfo>(`/api/stocks/${sid}`),

  getHistory: (sid: string, months: number) =>
    request<HistoryResponse>(`/api/stocks/${sid}/history?months=${months}`),

  /** Rule-based technical analysis. An AI counterpart will sit next to this. */
  getTraditionalAnalysis: (sid: string, months: number, ruleSet: RuleSet = 'grs') =>
    request<TraditionalAnalysisResponse>(
      `/api/stocks/${sid}/analysis/traditional?months=${months}&rule_set=${ruleSet}`,
    ),

  getRealtime: (sids: string[]) =>
    request<RealtimeResponse>(`/api/realtime?sids=${sids.join(',')}`),
}
