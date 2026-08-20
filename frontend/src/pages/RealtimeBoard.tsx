import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import RealtimeCard from '../components/RealtimeCard'
import StockSearch from '../components/StockSearch'
import { POLL_MS } from '../hooks/useLiveQuote'
import { useWatchlist } from '../hooks/useWatchlist'
import { isMarketOpen } from '../utils/market'
import { MAX_WATCHLIST } from '../watchlistStorage'

export default function RealtimeBoard() {
  // localStorage while signed out, the database once signed in -- either way a
  // plain string[], so the poll below is unaffected by which one is in play.
  const {
    sids: watchlist,
    add,
    remove,
    isFull,
    isLoading: watchlistLoading,
    error: watchlistError,
  } = useWatchlist()
  // Same rule the 大盤 and 個股 boards follow: outside the session a poll only
  // re-fetches the last tick, so it starts paused. The button still turns it on.
  const [live, setLive] = useState(isMarketOpen)

  const { data, error, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['realtime', watchlist],
    queryFn: () => api.getRealtime(watchlist),
    enabled: watchlist.length > 0,
    refetchInterval: live ? POLL_MS : false,
    staleTime: 0,
  })

  const errorEntries = Object.entries(data?.errors ?? {})

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <StockSearch
          onSelect={(stock) => add(stock.code)}
          placeholder="加入自選股，例如 2330"
          autoClearOnSelect
        />

        <div className="row wrap">
          <button
            type="button"
            className={`btn btn-sm ${live ? 'active' : ''}`}
            onClick={() => setLive((v) => !v)}
          >
            {live ? `自動更新中 (每 ${POLL_MS / 1000} 秒)` : '已暫停'}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            立即更新
          </button>
          {isFetching && <span className="spinner" />}
          {dataUpdatedAt > 0 && (
            <span className="dim">
              上次更新 {new Date(dataUpdatedAt).toLocaleTimeString('zh-TW')}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="banner banner-error">載入失敗：{(error as Error).message}</div>
      )}

      {watchlistError && (
        <div className="banner banner-error">
          自選股儲存失敗：{watchlistError.message}
        </div>
      )}

      {isFull && (
        <div className="banner banner-warn">
          自選股已達上限 {MAX_WATCHLIST} 檔，要再加入請先移除幾檔。
        </div>
      )}

      {errorEntries.length > 0 && (
        <div className="banner banner-warn">
          {errorEntries.map(([code, message]) => (
            <div key={code}>
              {code}：{message}
            </div>
          ))}
          <div className="dim" style={{ marginTop: 4 }}>
            即時報價僅在台股交易時段（週一至週五 09:00–13:30）提供。
          </div>
        </div>
      )}

      {watchlist.length === 0 ? (
        // Gated on the load finishing, or the empty state flashes while the
        // signed-in list is still on its way.
        <div className="center-note">
          {watchlistLoading ? (
            <span className="spinner" />
          ) : (
            '自選股是空的，用上方搜尋框加入股票'
          )}
        </div>
      ) : (
        <div className="quote-grid">
          {(data?.quotes ?? []).map((quote) => (
            <RealtimeCard key={quote.code} quote={quote} onRemove={remove} />
          ))}

          {/* Watchlist entries the upstream returned nothing for still need a way out. */}
          {watchlist
            .filter((code) => !(data?.quotes ?? []).some((q) => q.code === code))
            .map((code) => (
              <article className="card quote-card" key={code}>
                <button
                  type="button"
                  className="btn-icon remove"
                  title="移除"
                  onClick={() => remove(code)}
                >
                  ×
                </button>
                <div style={{ fontWeight: 700, fontSize: 17 }}>{code}</div>
                <p className="dim" style={{ marginTop: 8 }}>
                  {data?.errors?.[code] ?? (isFetching ? '載入中…' : '尚無報價')}
                </p>
              </article>
            ))}
        </div>
      )}
    </div>
  )
}
