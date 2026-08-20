import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import RealtimeCard from '../components/RealtimeCard'
import StockSearch from '../components/StockSearch'

const STORAGE_KEY = 'ai-stockboard.watchlist'
const DEFAULT_WATCHLIST = ['2330', '2317', '0050']
const POLL_MS = 10_000

function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_WATCHLIST
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : DEFAULT_WATCHLIST
  } catch {
    return DEFAULT_WATCHLIST
  }
}

export default function RealtimeBoard() {
  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist)
  const [live, setLive] = useState(true)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(watchlist))
  }, [watchlist])

  const { data, error, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['realtime', watchlist],
    queryFn: () => api.getRealtime(watchlist),
    enabled: watchlist.length > 0,
    refetchInterval: live ? POLL_MS : false,
    staleTime: 0,
  })

  function add(code: string) {
    setWatchlist((current) =>
      current.includes(code) ? current : [...current, code].slice(0, 20),
    )
  }

  function remove(code: string) {
    setWatchlist((current) => current.filter((c) => c !== code))
  }

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
        <div className="center-note">自選股是空的，用上方搜尋框加入股票</div>
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
