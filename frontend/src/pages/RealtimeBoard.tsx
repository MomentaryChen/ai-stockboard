import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { BestFourPointResult } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { readBoardView, saveBoardView, type BoardView } from '../boardPrefs'
import type { BoardEntry } from '../components/QuoteRow'
import RealtimeCard from '../components/RealtimeCard'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import WatchBoard from '../components/WatchBoard'
import { useDocumentPip } from '../hooks/useDocumentPip'
import { POLL_MS } from '../hooks/useLiveQuote'
import { useWatchlist } from '../hooks/useWatchlist'
import { useI18n } from '../i18n'
import { errorMessage } from '../utils/errors'
import { isMarketOpen } from '../utils/market'
import { MINI_WINDOW, miniUrl, useMiniView } from '../utils/view'
import { MAX_WATCHLIST } from '../watchlistStorage'

export default function RealtimeBoard() {
  const { status } = useAuth()
  const { intlTag, t } = useI18n()
  const authenticated = status === 'authenticated'
  // `?view=mini` is the chrome-free variant, meant to be opened in its own
  // small window; App drops the topbar for it.
  const mini = useMiniView()
  const pip = useDocumentPip()

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
  const [view, setView] = useState<BoardView>(readBoardView)

  const { data, error, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['realtime', watchlist],
    queryFn: () => api.getRealtime(watchlist),
    enabled: authenticated && watchlist.length > 0,
    refetchInterval: live ? POLL_MS : false,
    staleTime: 0,
  })

  // Daily bars, not ticks: independent of the quote poll so a 10-second refresh
  // does not re-score 20 stocks. The server still uses the same daily_price
  // cache the stock page fills.
  const analysis = useQuery({
    queryKey: ['analysis', 'traditional', 'batch', watchlist],
    queryFn: () => api.getTraditionalAnalysisBatch(watchlist),
    enabled: authenticated && watchlist.length > 0,
    staleTime: 60 * 60 * 1000,
  })

  const bfpBySid = useMemo(() => {
    const map = new Map<string, BestFourPointResult>()
    for (const item of analysis.data?.items ?? []) {
      map.set(item.sid, item.best_four_point)
    }
    return map
  }, [analysis.data])

  /**
   * One entry per watchlist code, in the watchlist's order.
   *
   * Built from the *watchlist* rather than from the response, so a code the
   * upstream could not quote keeps its place and its remove button instead of
   * being appended after the ones that worked -- and so the board's default
   * order is the order the user curated, which is what makes 'watchlist' a
   * sort worth returning to.
   */
  const entries = useMemo<BoardEntry[]>(() => {
    const byCode = new Map((data?.quotes ?? []).map((quote) => [quote.code, quote]))
    return watchlist.map((code) => {
      const quote = byCode.get(code)
      return {
        code,
        name: quote?.name,
        quote,
        bfp: bfpBySid.get(code),
        error: data?.errors?.[code],
      }
    })
  }, [watchlist, data, bfpBySid])

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
          <li>{t('signIn.realtimeBenefit4')}</li>
          <li>{t('signIn.realtimeBenefit3')}</li>
        </ul>
        <p className="dim" style={{ marginTop: 12 }}>
          {t('signIn.realtimeNote')}
        </p>
      </SignInPrompt>
    )
  }

  function setBoardView(next: BoardView) {
    setView(next)
    saveBoardView(next)
  }

  const liveButton = (
    <button
      type="button"
      className={`btn btn-sm ${live ? 'active' : ''}`}
      onClick={() => setLive((v) => !v)}
    >
      {live ? t('live.on', { seconds: POLL_MS / 1000 }) : t('live.off')}
    </button>
  )

  // The floating window and the mini window are both too narrow for the search
  // box and the full header, so curating the list stays a job for the full
  // board and those two only watch it.
  const board = (
    <WatchBoard
      entries={entries}
      onRemove={remove}
      bfpLoading={analysis.isPending}
      fetching={isFetching}
      compact={mini || pip.pipWindow !== null}
    />
  )

  const cards = (
    <div className="quote-grid">
      {entries.map((entry) =>
        entry.quote ? (
          <RealtimeCard
            key={entry.code}
            quote={entry.quote}
            onRemove={remove}
            bfp={entry.bfp}
            bfpLoading={analysis.isPending}
          />
        ) : (
          // Watchlist entries the upstream returned nothing for still need a way out.
          <article className="card quote-card" key={entry.code}>
            <button
              type="button"
              className="btn-icon remove"
              title={t('realtime.remove')}
              onClick={() => remove(entry.code)}
            >
              &times;
            </button>
            <div style={{ fontWeight: 700, fontSize: 17 }}>{entry.code}</div>
            <p className="dim" style={{ marginTop: 8 }}>
              {entry.error ?? (isFetching ? t('realtime.loading') : t('realtime.noQuote'))}
            </p>
          </article>
        ),
      )}
    </div>
  )

  if (mini) {
    return (
      <div className="stack mini-stack">
        <div className="row-between mini-bar">
          <div className="row" style={{ gap: 6 }}>
            {liveButton}
            {isFetching && <span className="spinner" />}
          </div>
          <Link to="/realtime" className="dim mini-full-link">
            {t('board.backToFull')}
          </Link>
        </div>
        {watchlist.length === 0 ? (
          <div className="center-note">
            {watchlistLoading ? <span className="spinner" /> : t('realtime.empty')}
          </div>
        ) : (
          board
        )}
      </div>
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
          <div className="segmented" title={t('board.viewTitle')}>
            <button
              type="button"
              className={`btn btn-sm ${view === 'list' ? 'active' : ''}`}
              onClick={() => setBoardView('list')}
            >
              {t('board.viewList')}
            </button>
            <button
              type="button"
              className={`btn btn-sm ${view === 'card' ? 'active' : ''}`}
              onClick={() => setBoardView('card')}
            >
              {t('board.viewCard')}
            </button>
          </div>

          {pip.supported && (
            <button
              type="button"
              className={`btn btn-sm ${pip.pipWindow ? 'active' : ''}`}
              title={t('board.popOutTitle')}
              onClick={() => (pip.pipWindow ? pip.close() : void pip.open(MINI_WINDOW))}
            >
              {pip.pipWindow ? t('board.popIn') : t('board.popOut')}
            </button>
          )}

          <button
            type="button"
            className="btn btn-sm"
            title={t('board.miniTitle')}
            onClick={() =>
              window.open(
                miniUrl('/realtime'),
                'ai-stockboard-mini',
                `width=${MINI_WINDOW.width},height=${MINI_WINDOW.height}`,
              )
            }
          >
            {t('board.miniOpen')}
          </button>

          {liveButton}
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
          {t('error.loadFailed', { message: errorMessage(error, t) })}
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
      ) : pip.pipWindow ? (
        // The board is mounted in the floating window; rendering it here too
        // would give the two copies separate sort and expansion state.
        <div className="center-note">
          <p style={{ margin: 0 }}>{t('board.popOutActive')}</p>
          <button
            type="button"
            className="btn btn-sm"
            style={{ marginTop: 12 }}
            onClick={() => pip.close()}
          >
            {t('board.popIn')}
          </button>
        </div>
      ) : view === 'card' ? (
        cards
      ) : (
        board
      )}

      {/* A portal, not a second React root: the floating window shares this
          tree's auth session, query cache and locale, so it cannot poll on its
          own schedule or show a different number. */}
      {pip.pipWindow &&
        createPortal(
          <div className="stack mini-stack pip-root">
            <div className="row-between mini-bar">
              <div className="row" style={{ gap: 6 }}>
                {liveButton}
                {isFetching && <span className="spinner" />}
              </div>
              <button type="button" className="btn btn-sm" onClick={() => pip.close()}>
                {t('board.popIn')}
              </button>
            </div>
            {board}
          </div>,
          pip.pipWindow.document.body,
        )}
    </div>
  )
}
