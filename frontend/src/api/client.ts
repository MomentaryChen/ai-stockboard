import type {
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
  getTraditionalAnalysis: (sid: string, months: number) =>
    request<TraditionalAnalysisResponse>(
      `/api/stocks/${sid}/analysis/traditional?months=${months}`,
    ),

  getRealtime: (sids: string[]) =>
    request<RealtimeResponse>(`/api/realtime?sids=${sids.join(',')}`),
}
