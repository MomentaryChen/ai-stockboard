import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { RuleSet } from '../api/types'
import AiVerdictSection from '../components/AiVerdict'
import BacktestCard from '../components/BacktestCard'
import BestFourPointCard from '../components/BestFourPointCard'
import ChenHoldCard from '../components/ChenHoldCard'
import ChipCard from '../components/ChipCard'
import DividendCard from '../components/DividendCard'
import HoldAiVerdict from '../components/HoldAiVerdict'
import MaPanel from '../components/MaPanel'
import PriceChart, { buildChartRows } from '../components/PriceChart'
import SignInPrompt from '../components/SignInPrompt'
import StockSearch from '../components/StockSearch'
import VolumeChart from '../components/VolumeChart'
import { POLL_MS, useLiveQuote } from '../hooks/useLiveQuote'
import { useI18n, type MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'
import { direction, fmtCompact, fmtInt, fmtLots, fmtPrice, fmtSigned } from '../utils/format'

const RANGES: Array<{ label: MessageKey; months: number }> = [
  { label: 'range.1m', months: 1 },
  { label: 'range.3m', months: 3 },
  { label: 'range.6m', months: 6 },
  { label: 'range.12m', months: 12 },
]

const MA_OPTIONS = ['ma5', 'ma10', 'ma20', 'ma60']

export default function StockDetail() {
  const { sid = '2330' } = useParams()
  const navigate = useNavigate()
  const { locale, t } = useI18n()

  const [months, setMonths] = useState(3)
  const [mode, setMode] = useState<'candle' | 'line'>('candle')
  const [visibleMas, setVisibleMas] = useState<string[]>(['ma5', 'ma20'])
  // Defaults to the corrected rules; 'twstock' is there to compare against.
  const [ruleSet, setRuleSet] = useState<RuleSet>('grs')

  const history = useQuery({
    queryKey: ['history', sid, months],
    queryFn: () => api.getHistory(sid, months),
  })

  const analysis = useQuery({
    queryKey: ['analysis', 'traditional', sid, months, ruleSet],
    queryFn: () => api.getTraditionalAnalysis(sid, months, ruleSet),
  })

  const backtest = useQuery({
    queryKey: ['backtest', sid, ruleSet],
    queryFn: () => api.getBacktest(sid, ruleSet),
    // A stock with too little history answers 422 for as long as that is
    // true; retrying turns one honest "not enough bars" into four.
    retry: false,
  })

  const dividends = useQuery({
    queryKey: ['dividends', sid],
    queryFn: () => api.getDividends(sid),
  })

  const chips = useQuery({
    queryKey: ['chips', sid],
    queryFn: () => api.getChips(sid),
    // Dates come from daily_price; without bars there is no trading calendar
    // to attach T86 to, so wait for history rather than returning empty.
    enabled: (history.data?.count ?? 0) > 0,
  })

  // Free, and independent of the `months` selector above: the hold snapshot
  // reads a fixed multi-year window, because "is this cheap" over a decade
  // must not change when somebody switches the chart to one month.
  //
  // Waits for the two queries whose caches it reads. The endpoint is
  // cache-only -- it never calls the exchange, which is what makes it public --
  // so running it first on a cold stock would score a company off no bars and
  // no dividends, then leave that on screen. Those two requests are what fill
  // the stores, and they are already on their way.
  const hold = useQuery({
    queryKey: ['chen', sid],
    queryFn: () => api.getChenAnalysis(sid),
    enabled: history.isSuccess && dividends.isSuccess,
  })

  const rows = useMemo(
    () => buildChartRows(history.data?.data ?? [], analysis.data?.ma_series),
    [history.data, analysis.data],
  )

  const lastClose = rows.at(-1)
  const {
    quote,
    locked,
    session,
    intraday,
    price,
    change,
    changePct,
    open,
    high,
    low,
    stamp,
    live,
    setLive,
    isFetching,
  } = useLiveQuote(sid, lastClose)

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
        <StockSearch onSelect={(stock) => navigate(`/stock/${stock.code}`)} />
      </div>

      {error && (
        <div className="banner banner-error">
          {t('error.loadFailed', { message: errorMessage(error, t) })}
          <br />
          <span className="dim">{t('error.dbHint')}</span>
        </div>
      )}

      <section className="card">
        <div className="row-between wrap">
          <div className="stock-head">
            <span className="sid">{sid}</span>
            <span className="sname">
              {history.data?.name ?? analysis.data?.name ?? quote?.name ?? ''}
            </span>
            {history.data && <span className="tag">{history.data.source.toUpperCase()}</span>}
            <span className="tag">
              {session === 'open' ? t('badge.marketOpen') : t('badge.marketClosed')}
            </span>
          </div>

          <div className="row wrap" style={{ gap: 16 }}>
            <div>
              <span className={`price-now ${dir}`}>{fmtPrice(price)}</span>{' '}
              <span className={`price-change ${dir}`}>
                {fmtSigned(change)}
                {changePct !== null ? ` (${fmtSigned(changePct)}%)` : ''}
              </span>
            </div>

            {/* Signed out there is nothing to poll, so the toggle gives way to
                the invitation rather than sitting there dead. */}
            {locked ? (
              <SignInPrompt compact title={t('signIn.stockTitle')} />
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
            <div className="stat-value">{fmtPrice(open)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.high')}</div>
            <div className="stat-value up">{fmtPrice(high)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.low')}</div>
            <div className="stat-value down">{fmtPrice(low)}</div>
          </div>
          <div>
            <div className="stat-label">{t('stat.volumeLots')}</div>
            {/* MIS already reports 張; the daily bar reports 股. */}
            <div className="stat-value">
              {intraday
                ? fmtInt(quote!.accumulate_trade_volume)
                : fmtLots(lastClose?.capacity ?? null)}
            </div>
          </div>
          <div>
            <div className="stat-label">{t('stat.tradingDays')}</div>
            <div className="stat-value">{rows.length}</div>
          </div>
          <div>
            <div className="stat-label">
              {intraday ? t('stat.quoteTime') : t('stat.lastDate')}
            </div>
            <div className="stat-value">{stamp}</div>
          </div>
          {dividends.data?.coverage === 'history' && (
            <div>
              <div className="stat-label">{t('dividend.yield')}</div>
              <div className="stat-value">
                {dividends.data.yield_percent === null
                  ? '--'
                  : `${fmtPrice(dividends.data.yield_percent)}%`}
              </div>
            </div>
          )}
        </div>

        {locked && (
          <p className="dim" style={{ margin: '12px 0 0' }}>
            {t('signIn.stockLockedNote')}
          </p>
        )}
      </section>

      {/* Full width, under the price and above the chart: the AI call is what
          this product leads with, and it spent its life at the bottom of the
          320px analysis column, below three cards and a table. Here it is
          across the page and it is still free to look at -- the panel only
          spends a generation when the button is pressed, so the rule engine's
          verdict in the stack below has lost nothing by moving down. */}
      <AiVerdictSection sid={sid} layout="spotlight" />

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
              {/* The evidence column: what the band above is judged
                  against. Everything here is deterministic and free except
                  the hold verdict at the foot of it, which is button-gated
                  for the same reason the band is. */}
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

          {/* A different question about the same stock, kept in its own
              section rather than folded into the band above or the cards
              beside it: those grade a position over days, this grades a
              company over years, and one blended verdict across both horizons
              would mean nothing. */}
          {hold.data && (
            <>
              <div className="section-label">{t('section.hold')}</div>
              <ChenHoldCard features={hold.data.features} rules={hold.data.rules} />
              <HoldAiVerdict sid={sid} />
            </>
          )}

          {chips.data && chips.data.coverage !== 'none' && (
            <>
              <div className="section-label">{t('section.chip')}</div>
              <ChipCard data={chips.data} />
            </>
          )}

          {dividends.data && dividends.data.coverage !== 'none' && (
            <DividendCard data={dividends.data} />
          )}

          <section className="card">
            <h2 className="card-title">{t('table.last10')}</h2>
            <table className="data">
              <thead>
                <tr>
                  <th>{t('table.date')}</th>
                  <th>{t('table.close')}</th>
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
                        <td>{fmtPrice(row.close)}</td>
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
