import { useCallback, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, MARKET_INDEX_SID, api } from '../api/client'
import type { RuleSet } from '../api/types'
import BacktestCard from '../components/BacktestCard'
import BestFourPointCard from '../components/BestFourPointCard'
import MaPanel from '../components/MaPanel'
import OpenIntelStrip from '../components/OpenIntelStrip'
import PriceChart, { buildChartRows } from '../components/PriceChart'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import VolumeChart from '../components/VolumeChart'
import { POLL_MS, useLiveQuote } from '../hooks/useLiveQuote'
import { useI18n, type MessageKey } from '../i18n'
import { direction, fmtCompact, fmtIndex, fmtLots, fmtSigned } from '../utils/format'
import { taipeiToday } from '../utils/market'
import { fromHistory, fromQuote, fromSnapshot, type OpenView } from '../utils/openIntel'

const RANGES: Array<{ label: MessageKey; months: number }> = [
  { label: 'range.1m', months: 1 },
  { label: 'range.3m', months: 3 },
  { label: 'range.6m', months: 6 },
  { label: 'range.12m', months: 12 },
]

const MA_OPTIONS = ['ma5', 'ma10', 'ma20', 'ma60']

/**
 * The landing page: the TAIEX, opened on today's session.
 *
 * The board answers "how did this day start" first and "where has the price
 * been" second, because that is the order the questions arrive in during a
 * session. The date defaults to today on the *exchange's* calendar and the
 * picker reaches back over settled sessions, so the same layout serves both
 * "what is happening now" and "what happened on the day I am thinking of".
 *
 * Three sources answer the opening question and the merge order matters:
 * a settled daily bar wins when one exists (it is final, and it carries the
 * turnover a quote does not), the live quote covers today until TWSE publishes
 * the report, and the chart's own history is the last resort -- which is what
 * lets a signed-out visitor still read today's board after the close.
 *
 * The index and nothing else. The watchlist is the /realtime board's subject
 * and it lives there in full -- quotes, the four points, add and remove. A
 * second, thinner copy of it under the index only ever answered the same
 * question worse, and it dragged a signed-in-only quote poll onto the one page
 * that has to work signed out.
 */
export default function MarketDashboard() {
  const navigate = useNavigate()
  const { locale, t } = useI18n()

  const today = taipeiToday()
  // The selected day lives in the URL, not in component state: a board showing
  // a particular session is worth linking to, and it makes the browser's Back
  // button undo a date change rather than leave the page. `?date=` is dropped
  // for today so the default landing URL stays bare.
  const [params, setParams] = useSearchParams()
  // Validated rather than trusted: the value reaches an API that answers 422 to
  // anything it cannot parse as a date, and a typed-in URL is the one input
  // path nothing else checks. Anything odd -- a malformed date, or one the
  // exchange has not reached -- silently means today.
  const requested = params.get('date')
  const date =
    requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) && requested <= today
      ? requested
      : today
  const isToday = date === today

  const setDate = useCallback(
    (next: string) => {
      setParams(
        (current) => {
          const updated = new URLSearchParams(current)
          if (!next || next === today) updated.delete('date')
          else updated.set('date', next)
          return updated
        },
        { replace: true },
      )
    },
    [setParams, today],
  )

  const [months, setMonths] = useState(3)
  const [mode, setMode] = useState<'candle' | 'line'>('candle')
  const [visibleMas, setVisibleMas] = useState<string[]>(['ma5', 'ma20'])
  // Defaults to the corrected rules; 'twstock' is there to compare against.
  const [ruleSet, setRuleSet] = useState<RuleSet>('grs')

  const history = useQuery({
    queryKey: ['history', MARKET_INDEX_SID, months],
    queryFn: () => api.getHistory(MARKET_INDEX_SID, months),
  })

  const analysis = useQuery({
    queryKey: ['analysis', 'traditional', MARKET_INDEX_SID, months, ruleSet],
    queryFn: () => api.getTraditionalAnalysis(MARKET_INDEX_SID, months, ruleSet),
  })

  const backtest = useQuery({
    queryKey: ['backtest', MARKET_INDEX_SID, ruleSet],
    queryFn: () => api.getBacktest(MARKET_INDEX_SID, ruleSet),
    // A stock with too little history answers 422 for as long as that is
    // true; retrying turns one honest "not enough bars" into four.
    retry: false,
  })

  // The index's settled bar for the selected day. Public and cache-only, so it
  // answers signed out too.
  const openBoard = useQuery({
    queryKey: ['market-open', date],
    queryFn: () => api.getMarketOpen(date),
  })

  const rows = useMemo(
    () => buildChartRows(history.data?.data ?? [], analysis.data?.ma_series),
    [history.data, analysis.data],
  )

  const lastClose = rows.at(-1)
  const { marketOpen, locked, quote, live, setLive, isFetching } = useLiveQuote(
    MARKET_INDEX_SID,
    lastClose,
  )

  /** The index's own row out of the open board, when the day has settled. */
  const settled = useMemo(
    () => (openBoard.data?.items ?? []).find((item) => item.sid === MARKET_INDEX_SID) ?? null,
    [openBoard.data],
  )

  /** The selected day, straight from the history already on the page. */
  const indexFromHistory = useMemo(
    () =>
      fromHistory(history.data?.data ?? [], date, t('market.index'), MARKET_INDEX_SID),
    [history.data, date, t],
  )

  const indexView = useMemo<OpenView | null>(() => {
    if (settled) return fromSnapshot(settled)
    if (isToday && quote && quote.open !== null) return fromQuote(quote, date)
    return indexFromHistory
  }, [settled, isToday, quote, date, indexFromHistory])

  // Today before the report lands and with no quote to read -- a signed-out
  // visitor mid-session. Falling back to the last settled bar is what the board
  // did before the date picker existed, and it beats an empty card; the badge
  // and the note below say which day is on screen.
  const fallbackView = useMemo<OpenView | null>(() => {
    if (indexView || !isToday || !lastClose) return null
    return fromHistory(
      history.data?.data ?? [],
      lastClose.date,
      t('market.index'),
      MARKET_INDEX_SID,
    )
  }, [indexView, isToday, lastClose, history.data, t])

  const shown = indexView ?? fallbackView
  const stale = indexView === null && fallbackView !== null

  const latestTradingDay = openBoard.data?.latest_trading_day ?? null
  const dir = direction(shown?.change)
  const badge = !isToday
    ? t('open.badgeHistory')
    : shown?.intraday
      ? t('badge.marketOpen')
      : t('badge.marketClosed')
  const stamp = shown?.intraday && quote ? quote.time.slice(11) : (shown?.date ?? '--')

  // What the card has to admit about itself, most surprising first. The date
  // picker says one day and the figures can be from another -- a signed-out
  // visitor mid-session is reading the last settled close -- and saying so is
  // the difference between a stale board and a wrong one.
  const notes = useMemo(() => {
    const lines: string[] = []
    if (!shown) return lines
    if (stale) lines.push(t('open.showingLastSession', { date: shown.date }))
    if (isToday && locked) {
      lines.push(t('signIn.marketLockedNote', { seconds: POLL_MS / 1000 }))
    } else if (isToday && shown.intraday) {
      lines.push(t('open.pendingReport'))
    } else if (isToday && !marketOpen && !stale) {
      lines.push(t('market.offHoursNote'))
    }
    return lines
  }, [shown, stale, isToday, locked, marketOpen, t])

  function toggleMa(key: string) {
    setVisibleMas((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    )
  }

  const error = history.error ?? analysis.error

  return (
    <div className="stack">
      <div className="row wrap" style={{ gap: 16 }}>
        <StockSearch
          onSelect={(stock) => navigate(`/stock/${stock.code}`)}
          placeholder={t('search.placeholderStock')}
        />
      </div>

      {error && (
        <div className="banner banner-error">
          {t('error.loadFailed', { message: (error as Error).message })}
          <br />
          <span className="dim">{t('error.dbHint')}</span>
        </div>
      )}

      <section className="card">
        <div className="row-between wrap">
          <div className="stock-head">
            <span className="sname" style={{ fontSize: 22, color: 'var(--text)' }}>
              {t('market.index')}
            </span>
            <span className="tag">{badge}</span>
            <span className="dim">{t('market.indexSubtitle')}</span>
          </div>

          <div className="row wrap" style={{ gap: 16 }}>
            <div className="row">
              <label className="dim" htmlFor="open-date">
                {t('open.date')}
              </label>
              <input
                id="open-date"
                type="date"
                className="text-input date-input"
                value={date}
                // The exchange has not reached tomorrow, so neither has the
                // board. An empty field (the picker's clear button) means today.
                max={today}
                onChange={(event) => setDate(event.target.value || today)}
              />
              <button
                type="button"
                className={`btn btn-sm ${isToday ? 'active' : ''}`}
                onClick={() => setDate(today)}
              >
                {t('open.today')}
              </button>
            </div>

            {shown && (
              <div>
                <span className={`price-now ${dir}`}>{fmtIndex(shown.last)}</span>{' '}
                <span className={`price-change ${dir}`}>
                  {fmtSigned(shown.change)}
                  {shown.changePct != null ? ` (${fmtSigned(shown.changePct)}%)` : ''}
                </span>
              </div>
            )}

            {/* The live toggle only means anything on today's session: a past
                day is settled, and polling would re-fetch a quote about a
                different date. */}
            {isToday &&
              (locked ? (
                <SignInPrompt compact title={t('signIn.marketTitle')} />
              ) : (
                <div className="row wrap">
                  <button
                    type="button"
                    className={`btn btn-sm ${live ? 'active' : ''}`}
                    onClick={() => setLive((v) => !v)}
                  >
                    {live ? t('live.on', { seconds: POLL_MS / 1000 }) : t('live.off')}
                  </button>
                  {isFetching && <span className="spinner" />}
                </div>
              ))}
          </div>
        </div>

        {shown ? (
          <>
            <OpenIntelStrip
              view={shown}
              format={fmtIndex}
              turnoverFallback={indexFromHistory?.turnover}
              locale={locale}
            />

            <div className="stat-grid" style={{ marginTop: 16 }}>
              <div>
                <div className="stat-label">{t('stat.open')}</div>
                <div className="stat-value">{fmtIndex(shown.open)}</div>
              </div>
              <div>
                <div className="stat-label">{t('stat.high')}</div>
                <div className="stat-value up">{fmtIndex(shown.high)}</div>
              </div>
              <div>
                <div className="stat-label">{t('stat.low')}</div>
                <div className="stat-value down">{fmtIndex(shown.low)}</div>
              </div>
              <div>
                <div className="stat-label">{t('quote.prevClose')}</div>
                <div className="stat-value">{fmtIndex(shown.prevClose)}</div>
              </div>
              <div>
                <div className="stat-label">{t('stat.volumeLots')}</div>
                <div className="stat-value">{fmtLots(shown.capacity)}</div>
              </div>
              <div>
                <div className="stat-label">
                  {shown.intraday ? t('stat.quoteTime') : t('stat.lastClose')}
                </div>
                <div className="stat-value">{stamp}</div>
              </div>
            </div>
          </>
        ) : (
          <div className="center-note" style={{ padding: '32px 12px' }}>
            {openBoard.isPending ? (
              <span className="spinner" />
            ) : openBoard.error ? (
              // "No session" would be a lie about a day that did trade, so a
              // failed lookup has to say it failed.
              <div>{t('error.loadFailed', { message: (openBoard.error as Error).message })}</div>
            ) : (
              <>
                <div>{t('open.noSession', { date })}</div>
                {latestTradingDay && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    style={{ marginTop: 12 }}
                    onClick={() => setDate(latestTradingDay)}
                  >
                    {t('open.jumpLatest', { date: latestTradingDay })}
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {notes.map((note) => (
          <p className="dim" style={{ margin: '12px 0 0' }} key={note}>
            {note}
          </p>
        ))}
      </section>

      <div className="grid-detail">
        <section className="card">
          <div className="row-between wrap" style={{ marginBottom: 12 }}>
            <div className="segmented">
              {RANGES.map((range) => (
                <button
                  key={range.months}
                  type="button"
                  className={`btn btn-sm ${months === range.months ? 'active' : ''}`}
                  onClick={() => setMonths(range.months)}
                >
                  {t(range.label)}
                </button>
              ))}
            </div>

            <div className="row wrap" style={{ gap: 12 }}>
              <div className="segmented">
                <button
                  type="button"
                  className={`btn btn-sm ${mode === 'candle' ? 'active' : ''}`}
                  onClick={() => setMode('candle')}
                >
                  {t('chart.candle')}
                </button>
                <button
                  type="button"
                  className={`btn btn-sm ${mode === 'line' ? 'active' : ''}`}
                  onClick={() => setMode('line')}
                >
                  {t('chart.line')}
                </button>
              </div>

              <div className="segmented">
                {MA_OPTIONS.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={`btn btn-sm ${visibleMas.includes(key) ? 'active' : ''}`}
                    onClick={() => toggleMa(key)}
                  >
                    {key.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {history.isLoading ? (
            <div className="center-note">
              <div className="row" style={{ justifyContent: 'center' }}>
                <span className="spinner" />
                <span style={{ marginLeft: 10 }}>{t('chart.firstFetchNote')}</span>
              </div>
            </div>
          ) : rows.length === 0 ? (
            <div className="center-note">{t('common.noData')}</div>
          ) : (
            <>
              <PriceChart rows={rows} mode={mode} visibleMas={visibleMas} />
              <VolumeChart rows={rows} />
            </>
          )}

          {history.data && (
            <p className="dim" style={{ marginTop: 10 }}>
              {t('history.fetchNote', {
                fetched: history.data.fetched_months.length,
                cached: history.data.cached_months.length,
                detail:
                  history.data.fetched_months.length > 0
                    ? t('history.fetchDetail', {
                        months: history.data.fetched_months.join(', '),
                      })
                    : '',
              })}
            </p>
          )}
        </section>

        <div className="stack">
          {analysis.data && (
            <>
              {/* The same rule-based engine the stock page uses, run on the index. */}
              <div className="section-label">{t('section.traditional')}</div>
              <BestFourPointCard
                result={analysis.data.best_four_point}
                asOf={analysis.data.as_of}
                sampleSize={analysis.data.sample_size}
                ruleSet={ruleSet}
                onRuleSetChange={setRuleSet}
              />
              <MaPanel
                mas={analysis.data.moving_averages}
                latestClose={analysis.data.latest_close}
              />
              {/* Under the verdict it grades, and sharing its rule set: the
                  card above says Buy, this one says what Buy has been worth. */}
              {backtest.data ? (
                <BacktestCard data={backtest.data} />
              ) : backtest.isError ? (
                <section className="card">
                  <h2 className="card-title">{t('bt.title')}</h2>
                  <p className="dim" style={{ margin: 0 }}>
                    {backtest.error instanceof ApiError &&
                    backtest.error.status === 422
                      ? t('bt.notEnoughBars')
                      : t('bt.failed', {
                          message: (backtest.error as Error).message,
                        })}
                  </p>
                </section>
              ) : null}
            </>
          )}

          <section className="card">
            <h2 className="card-title">{t('table.last10')}</h2>
            <table className="data">
              <thead>
                <tr>
                  <th>{t('table.date')}</th>
                  <th>{t('table.closeIndex')}</th>
                  <th>{t('table.change')}</th>
                  <th>{t('table.turnover')}</th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .slice(-10)
                  .reverse()
                  .map((row) => {
                    const point = history.data?.data.find((d) => d.date === row.date)
                    return (
                      // Clicking a day takes the open board to it -- the table
                      // is the shortest route to "what happened on the 14th".
                      <tr
                        key={row.date}
                        className={`row-pick ${row.date === date ? 'row-active' : ''}`}
                        onClick={() => setDate(row.date)}
                      >
                        <td>{row.date.slice(5)}</td>
                        <td>{fmtIndex(row.close)}</td>
                        <td className={direction(row.change)}>{fmtSigned(row.change)}</td>
                        <td>{fmtCompact(point?.turnover ?? null, locale)}</td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  )
}
