import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import RealtimeCard from '../components/RealtimeCard'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import { POLL_MS } from '../hooks/useLiveQuote'
import { useWatchlist } from '../hooks/useWatchlist'
import { useI18n } from '../i18n'
import { isMarketOpen } from '../utils/market'
import { MAX_WATCHLIST } from '../watchlistStorage'

export default function RealtimeBoard() {
  const { status } = useAuth()
  const { intlTag, t } = useI18n()
  const authenticated = status === 'authenticated'

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
  // Same rule the market and stock boards follow: outside the session a poll
  // only re-fetches the last tick, so it starts paused. The button still turns
  // it on.
  const [live, setLive] = useState(isMarketOpen)

  const { data, error, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['realtime', watchlist],
    queryFn: () => api.getRealtime(watchlist),
    enabled: authenticated && watchlist.length > 0,
    refetchInterval: live ? POLL_MS : false,
    staleTime: 0,
  })

  const errorEntries = Object.entries(data?.errors ?? {})

  // Restoring a session on a hard refresh -- see the note in RequireAuth: a
  // prompt rendered here would flash at somebody who is already signed in.
  if (status === 'loading') {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }

  if (!authenticated) {
    const seconds = POLL_MS / 1000
    return (
      <SignInPrompt title={t('signIn.realtimeTitle')}>
        <p className="prompt-lead">{t('signIn.realtimeLead', { seconds })}</p>
        <ul className="reason-list">
          <li>{t('signIn.realtimeBenefit1', { max: MAX_WATCHLIST, seconds })}</li>
          <li>{t('signIn.realtimeBenefit2')}</li>
          <li>{t('signIn.realtimeBenefit3')}</li>
        </ul>
        <p className="dim" style={{ marginTop: 12 }}>
          {t('signIn.realtimeNote')}
        </p>
      </SignInPrompt>
    )
  }

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <StockSearch
          onSelect={(stock) => add(stock.code)}
          placeholder={t('search.placeholderWatchlist')}
          autoClearOnSelect
        />

        <div className="row wrap">
          <button
            type="button"
            className={`btn btn-sm ${live ? 'active' : ''}`}
            onClick={() => setLive((v) => !v)}
          >
            {live ? t('live.on', { seconds: POLL_MS / 1000 }) : t('live.off')}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            {t('realtime.refreshNow')}
          </button>
          {isFetching && <span className="spinner" />}
          {dataUpdatedAt > 0 && (
            <span className="dim">
              {t('realtime.lastUpdated', {
                time: new Date(dataUpdatedAt).toLocaleTimeString(intlTag),
              })}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="banner banner-error">
          {t('error.loadFailed', { message: (error as Error).message })}
        </div>
      )}

      {watchlistError && (
        <div className="banner banner-error">
          {t('realtime.watchlistSaveFailed', { message: watchlistError.message })}
        </div>
      )}

      {isFull && (
        <div className="banner banner-warn">
          {t('realtime.watchlistFull', { max: MAX_WATCHLIST })}
        </div>
      )}

      {errorEntries.length > 0 && (
        <div className="banner banner-warn">
          {errorEntries.map(([code, message]) => (
            <div key={code}>{t('realtime.quoteError', { code, message })}</div>
          ))}
          <div className="dim" style={{ marginTop: 4 }}>
            {t('realtime.sessionNote')}
          </div>
        </div>
      )}

      {watchlist.length === 0 ? (
        // Gated on the load finishing, or the empty state flashes while the
        // signed-in list is still on its way.
        <div className="center-note">
          {watchlistLoading ? <span className="spinner" /> : t('realtime.empty')}
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
                  title={t('realtime.remove')}
                  onClick={() => remove(code)}
                >
                  ×
                </button>
                <div style={{ fontWeight: 700, fontSize: 17 }}>{code}</div>
                <p className="dim" style={{ marginTop: 8 }}>
                  {data?.errors?.[code] ??
                    (isFetching ? t('realtime.loading') : t('realtime.noQuote'))}
                </p>
              </article>
            ))}
        </div>
      )}
    </div>
  )
}
