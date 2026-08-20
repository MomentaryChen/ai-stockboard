import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { MARKET_INDEX_SID, api } from '../api/client'
import type { RuleSet } from '../api/types'
import BestFourPointCard from '../components/BestFourPointCard'
import MaPanel from '../components/MaPanel'
import PriceChart, { buildChartRows } from '../components/PriceChart'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import VolumeChart from '../components/VolumeChart'
import { POLL_MS, useLiveQuote } from '../hooks/useLiveQuote'
import { useI18n, type MessageKey } from '../i18n'
import { direction, fmtCompact, fmtIndex, fmtLots, fmtSigned } from '../utils/format'

const RANGES: Array<{ label: MessageKey; months: number }> = [
  { label: 'range.1m', months: 1 },
  { label: 'range.3m', months: 3 },
  { label: 'range.6m', months: 6 },
  { label: 'range.12m', months: 12 },
]

const MA_OPTIONS = ['ma5', 'ma10', 'ma20', 'ma60']

/** The landing page: the TAIEX -- intraday level, history, and the same
 *  rule-based analysis an individual stock gets. */
export default function MarketDashboard() {
  const navigate = useNavigate()
  const { locale, t } = useI18n()

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

  const rows = useMemo(
    () => buildChartRows(history.data?.data ?? [], analysis.data?.ma_series),
    [history.data, analysis.data],
  )

  const lastClose = rows.at(-1)
  const {
    marketOpen,
    locked,
    session,
    intraday,
    price: level,
    change,
    changePct,
    open,
    high,
    low,
    stamp,
    live,
    setLive,
    isFetching,
  } = useLiveQuote(MARKET_INDEX_SID, lastClose)

  const dir = direction(change)

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
            <span className="tag">
              {session === 'open' ? t('badge.marketOpen') : t('badge.marketClosed')}
            </span>
            <span className="dim">{t('market.indexSubtitle')}</span>
          </div>

          <div className="row wrap" style={{ gap: 16 }}>
            <div>
              <span className={`price-now ${dir}`}>{fmtIndex(level)}</span>{' '}
              <span className={`price-change ${dir}`}>
                {fmtSigned(change)}
                {changePct !== null ? ` (${fmtSigned(changePct)}%)` : ''}
              </span>
            </div>

            {/* Signed out there is nothing to poll, so the toggle gives way to
                the invitation rather than sitting there dead. */}
            {locked ? (
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
            )}
          </div>
        </div>

        <div className="stat-grid" style={{ marginTop: 16 }}>
          <div>
            <div className="stat-label">{t('stat.open')}</div>
            <div className="stat-value">{fmtIndex(open)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.high')}</div>
            <div className="stat-value up">{fmtIndex(high)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.low')}</div>
            <div className="stat-value down">{fmtIndex(low)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.turnover')}</div>
            <div className="stat-value">
              {fmtCompact(history.data?.data.at(-1)?.turnover ?? null, locale)}
            </div>
          </div>
          <div>
            <div className="stat-label">{t('stat.volumeLots')}</div>
            <div className="stat-value">{fmtLots(lastClose?.capacity ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">
              {intraday ? t('stat.quoteTime') : t('stat.lastClose')}
            </div>
            <div className="stat-value">{stamp}</div>
          </div>
        </div>

        {locked ? (
          <p className="dim" style={{ margin: '12px 0 0' }}>
            {t('signIn.marketLockedNote', { seconds: POLL_MS / 1000 })}
          </p>
        ) : (
          !marketOpen && (
            <p className="dim" style={{ margin: '12px 0 0' }}>
              {t('market.offHoursNote')}
            </p>
          )
        )}
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
                      <tr key={row.date}>
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
