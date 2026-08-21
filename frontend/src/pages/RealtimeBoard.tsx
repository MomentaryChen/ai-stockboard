import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { BestFourPointResult } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import {
  readBoardGroup,
  readBoardView,
  saveBoardGroup,
  saveBoardView,
  type BoardView,
  type GroupFilter,
} from '../boardPrefs'
import { aiVerdictKey } from '../components/AiVerdict'
import type { BoardEntry } from '../components/QuoteRow'
import RealtimeCard from '../components/RealtimeCard'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import WatchBoard from '../components/WatchBoard'
import WatchlistGroups from '../components/WatchlistGroups'
import { useDocumentPip } from '../hooks/useDocumentPip'
import { useGroupDrop } from '../hooks/useGroupDrop'
import { POLL_MS } from '../hooks/useLiveQuote'
import { useWatchlist } from '../hooks/useWatchlist'
import { useI18n } from '../i18n'
import { errorMessage } from '../utils/errors'
import { isMarketOpen } from '../utils/market'
import { MINI_WINDOW, miniUrl, useMiniView } from '../utils/view'
import { groupSections } from '../utils/watchlistGroups'
import { MAX_WATCHLIST } from '../watchlistStorage'

export default function RealtimeBoard() {
  const { status } = useAuth()
  const { intlTag, locale, t } = useI18n()
  const queryClient = useQueryClient()
  const authenticated = status === 'authenticated'
  // `?view=mini` is the chrome-free variant, meant to be opened in its own
  // small window; App drops the topbar for it.
  const mini = useMiniView()
  const pip = useDocumentPip()

  // localStorage while signed out, the database once signed in -- either way a
  // plain string[], so the poll below is unaffected by which one is in play.
  const {
    sids: watchlist,
    groups,
    groupBySid,
    add,
    remove,
    assign,
    createGroup,
    renameGroup,
    deleteGroup,
    isFull,
    isLoading: watchlistLoading,
    error: watchlistError,
  } = useWatchlist()
  // Same rule the market and stock boards follow: outside the session a poll
  // only re-fetches the last tick, so it starts paused. The button still turns
  // it on.
  const [live, setLive] = useState(isMarketOpen)
  const [view, setView] = useState<BoardView>(readBoardView)
  const [filter, setFilter] = useState<GroupFilter>(readBoardGroup)
  // The list board keeps its own drop state inside WatchBoard; the card grid
  // has no such component to hold it.
  const cardDrop = useGroupDrop(groupBySid, assign)

  useEffect(() => {
    if (filter.kind === 'group' && !groups.some((group) => group.id === filter.id)) {
      setFilter({ kind: 'all' })
      saveBoardGroup({ kind: 'all' })
    }
  }, [filter, groups])

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

  /**
   * Verdicts the board already has, in one request instead of one per card.
   *
   * The card view mounts an AI panel per row, and each would otherwise read its
   * own -- twenty connections asking twenty questions that one query answers.
   * Nothing here can generate anything: `/api/analysis/ai` is cache-only, so a
   * board load stays free however many rows it has.
   *
   * Keyed on the locale as well as the watchlist because a verdict is prose the
   * model wrote in one language, and the panels key their cache the same way.
   */
  const aiVerdicts = useQuery({
    queryKey: ['ai-verdict', 'batch', watchlist, locale],
    queryFn: () => api.getAiAnalysisBatch(watchlist, locale),
    enabled: authenticated && watchlist.length > 0,
    staleTime: 60 * 60 * 1000,
  })

  // Seeded into the per-panel keys rather than passed down as a prop: the panel
  // writes that same key when the button is pressed, so one place holds the
  // verdict whether it was read or paid for.
  useEffect(() => {
    const items = aiVerdicts.data?.items
    if (!items) return
    const found = new Set(items.map((item) => item.sid))
    for (const item of items) {
      queryClient.setQueryData(aiVerdictKey(item.sid, locale), item)
    }
    // An absent sid is a real answer -- "nobody has generated one" -- and has
    // to be written too, or the panel would fall back to reading it alone and
    // the batch would have saved nothing.
    for (const code of watchlist) {
      if (!found.has(code)) {
        queryClient.setQueryData(aiVerdictKey(code, locale), null)
      }
    }
  }, [aiVerdicts.data, watchlist, locale, queryClient])

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

  /**
   * The quote poll still asks for every sid. Filtering here only hides rows,
   * so switching groups never drops a request that was already paid for.
   */
  const visible = useMemo(() => {
    if (filter.kind === 'all') return entries
    if (filter.kind === 'ungrouped') {
      return entries.filter((entry) => groupBySid[entry.code] == null)
    }
    return entries.filter((entry) => groupBySid[entry.code] === filter.id)
  }, [entries, filter, groupBySid])

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

  function setBoardGroup(next: GroupFilter) {
    setFilter(next)
    saveBoardGroup(next)
  }

  const addingTo = filter.kind === 'group' ? filter.id : null

  /**
   * "All" is not the opposite of the folders -- it is every folder at once.
   *
   * Filtering to one group answers "what is in this one?"; the whole list still
   * has to answer "where does everything sit?", and a flat twenty rows cannot.
   * The headings are also the drop targets, so the view that shows every group
   * is the view where a stock can be moved between any two of them.
   */
  const showGrouped = filter.kind === 'all' && groups.length > 0

  const liveButton = (
    <button
      type="button"
      className={`btn btn-sm ${live ? 'active' : ''}`}
      onClick={() => setLive((v) => !v)}
    >
      {live ? t('live.on', { seconds: POLL_MS / 1000 }) : t('live.off')}
    </button>
  )

  const groupBar = (
    <WatchlistGroups
      groups={groups}
      groupBySid={groupBySid}
      sids={watchlist}
      filter={filter}
      onFilter={setBoardGroup}
      onCreate={async (name) => {
        const result = await createGroup(name)
        const created = result.groups[result.groups.length - 1]
        if (created) setBoardGroup({ kind: 'group', id: created.id })
      }}
      onRename={renameGroup}
      onDelete={deleteGroup}
      onAssign={assign}
      compact={mini || pip.pipWindow !== null}
    />
  )

  // The floating window and the mini window are both too narrow for the search
  // box and the full header, so curating the list stays a job for the full
  // board and those two only watch it.
  const board = (
    <WatchBoard
      entries={visible}
      onRemove={remove}
      groups={groups}
      groupBySid={groupBySid}
      onAssign={assign}
      grouped={showGrouped}
      bfpLoading={analysis.isPending}
      fetching={isFetching}
      compact={mini || pip.pipWindow !== null}
    />
  )

  function card(entry: BoardEntry) {
    if (entry.quote) {
      return (
        <RealtimeCard
          key={entry.code}
          quote={entry.quote}
          onRemove={remove}
          groups={groups}
          groupId={groupBySid[entry.code] ?? null}
          onAssign={assign}
          dragging={cardDrop.dragging === entry.code}
          onDragStateChange={cardDrop.setDragging}
          bfp={entry.bfp}
          bfpLoading={analysis.isPending}
          aiBatched
          // `isLoading`, not `isPending`: this one gates a button, and a
          // disabled query is pending forever -- which would leave the panel
          // unclickable rather than merely un-spinnered.
          aiLoading={aiVerdicts.isLoading}
        />
      )
    }
    // Watchlist entries the upstream returned nothing for still need a way out.
    return (
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
    )
  }

  const cards = showGrouped ? (
    <div className="stack">
      {groupSections(visible, groups, groupBySid, t('board.groupUngrouped')).map((section) => {
        const target = cardDrop.target(section.id)
        return (
          <section
            key={section.id ?? 'ungrouped'}
            className={`card-group${target.className}`}
            {...target.handlers}
          >
            <h3 className="group-section-title">
              {section.name}
              <span className="group-count">{section.items.length}</span>
            </h3>
            {section.items.length === 0 ? (
              <p className="group-section-empty">{t('board.groupEmptyDrop')}</p>
            ) : (
              <div className="quote-grid">{section.items.map(card)}</div>
            )}
          </section>
        )
      })}
    </div>
  ) : (
    <div className="quote-grid">{visible.map(card)}</div>
  )

  const emptyNote =
    watchlist.length === 0
      ? t('realtime.empty')
      : filter.kind === 'ungrouped'
        ? t('realtime.emptyUngrouped')
        : t('realtime.emptyGroup')

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
        {groupBar}
        {watchlist.length === 0 || visible.length === 0 ? (
          <div className="center-note">
            {watchlistLoading ? <span className="spinner" /> : emptyNote}
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
          onSelect={(stock) => add(stock.code, addingTo)}
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

      {groupBar}

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
      ) : visible.length === 0 ? (
        <div className="center-note">{emptyNote}</div>
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
            {groupBar}
            {visible.length === 0 ? (
              <div className="center-note">{emptyNote}</div>
            ) : (
              board
            )}
          </div>,
          pip.pipWindow.document.body,
        )}
    </div>
  )
}
