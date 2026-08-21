import { useMemo } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { BacktestResponse } from '../api/types'
import { useI18n } from '../i18n'
import {
  direction,
  fmtPercent,
  fmtPoints,
  fmtSignedPercent,
  shortDate,
} from '../utils/format'

const STRATEGY = '#4c8dff'
const BUY_HOLD = '#8a91a3'

/**
 * What the four-point verdict was actually worth over the past year.
 *
 * The card exists to stop the Buy/Sell badge above it from being taken on
 * faith, so its job is as much to report a bad result clearly as a good one --
 * and on most Taiwan stocks over a rising year the result *is* bad, because
 * the rule spends most of its time in cash.
 *
 * Three deliberate choices about what gets prominence:
 *
 *   * **Buy-and-hold sits next to the strategy return, always.** A strategy
 *     that made +2% looks like a win until you see the stock made +85%. The
 *     comparison is the finding, so it is not something the reader has to go
 *     looking for.
 *   * **Exposure is a headline figure, not a footnote.** It is the thing that
 *     explains an apparently excellent hit rate producing a mediocre return:
 *     a rule in the market 12% of the time cannot capture a rally however
 *     often it is right.
 *   * **Every hit rate is shown against the baseline.** Alone, "58% of Buys
 *     went up" reads as a signal; against a window where 63% of *all* days
 *     went up it reads as noise, which is what it is. The edge column does
 *     that subtraction rather than leaving it to the reader.
 *
 * The rule-set switch lives on the Best Four Point card above and is passed
 * down: two switches that could disagree about which rules are on screen would
 * be worse than one.
 */
export default function BacktestCard({ data }: { data: BacktestResponse }) {
  const { t } = useI18n()
  const sim = data.simulation

  const curve = useMemo(
    () =>
      sim.equity.map((point) => ({
        date: point.date,
        label: shortDate(point.date),
        // Plotted as return rather than as an index: '+85%' is read directly,
        // '1.85' has to be converted by the reader first.
        strategy: (point.strategy - 1) * 100,
        buyHold: (point.buy_hold - 1) * 100,
      })),
    [sim.equity],
  )

  const byHorizon = useMemo(() => {
    const sell = new Map(data.sell_stats.map((s) => [s.horizon, s]))
    const base = new Map(data.baseline.map((b) => [b.horizon, b]))
    const edge = new Map(data.edges.map((e) => [e.horizon, e]))
    return data.buy_stats.map((buy) => ({
      horizon: buy.horizon,
      buy,
      sell: sell.get(buy.horizon),
      base: base.get(buy.horizon),
      edge: edge.get(buy.horizon),
    }))
  }, [data])

  const beat = sim.strategy_return - sim.buy_hold_return
  const pending = data.buy_stats.concat(data.sell_stats).some((s) => s.pending > 0)

  return (
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('bt.title')}
        </h2>
        <span className="tag">{t('bt.window', { months: data.window_months })}</span>
      </div>

      {/* The headline: did following the signal beat ignoring it. */}
      <p style={{ margin: '0 0 14px', lineHeight: 1.6 }}>
        <span className={`stat-value ${direction(beat)}`} style={{ fontSize: 18 }}>
          {beat >= 0 ? t('bt.beatBuyHold') : t('bt.trailBuyHold')}
        </span>
        <br />
        <span className="dim">
          {t('bt.headlineDetail', {
            strategy: fmtSignedPercent(sim.strategy_return),
            buyHold: fmtSignedPercent(sim.buy_hold_return),
            exposure: fmtPercent(sim.exposure, 0),
          })}
        </span>
      </p>

      <div className="stat-grid">
        <div>
          <div className="stat-label">{t('bt.strategyReturn')}</div>
          <div className={`stat-value ${direction(sim.strategy_return)}`}>
            {fmtSignedPercent(sim.strategy_return)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('bt.buyHoldReturn')}</div>
          <div className={`stat-value ${direction(sim.buy_hold_return)}`}>
            {fmtSignedPercent(sim.buy_hold_return)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('bt.tradeWinRate')}</div>
          <div className="stat-value">
            {sim.trade_count === 0 ? '--' : fmtPercent(sim.trade_win_rate, 0)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('bt.trades')}</div>
          <div className="stat-value">
            {t('bt.tradeCount', {
              count: sim.trade_count,
              wins: sim.winning_trades,
            })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('bt.exposure')}</div>
          <div className="stat-value">{fmtPercent(sim.exposure, 0)}</div>
        </div>
        <div>
          <div className="stat-label">{t('bt.maxDrawdown')}</div>
          <div className="stat-value down">
            {fmtSignedPercent(sim.max_drawdown)}
          </div>
        </div>
      </div>

      {curve.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={curve} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                width={48}
              />
              {/* Break-even. Without it the reader has to find zero on the
                  axis to tell a losing curve from a winning one. */}
              <ReferenceLine y={0} stroke="var(--border-strong)" />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value: number, name: string) => [
                  `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`,
                  name,
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="strategy"
                name={t('bt.legendStrategy')}
                stroke={STRATEGY}
                dot={false}
                strokeWidth={2}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="buyHold"
                name={t('bt.legendBuyHold')}
                stroke={BUY_HOLD}
                dot={false}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <h3 className="card-title" style={{ fontSize: 14, margin: '18px 0 8px' }}>
        {t('bt.hitTitle')}
      </h3>

      {data.signal_count === 0 ? (
        <p className="dim" style={{ margin: 0 }}>
          {t('bt.noSignals')}
        </p>
      ) : (
        <>
          <table className="data">
            <thead>
              <tr>
                <th>{t('bt.horizon')}</th>
                <th>{t('bt.buyHit')}</th>
                <th>{t('bt.sellHit')}</th>
                <th>{t('bt.baseline')}</th>
                <th>{t('bt.edge')}</th>
              </tr>
            </thead>
            <tbody>
              {byHorizon.map(({ horizon, buy, sell, base, edge }) => (
                <tr key={horizon}>
                  <td>{t('bt.horizonDays', { days: horizon })}</td>
                  <td>
                    {fmtPercent(buy.win_rate, 0)}{' '}
                    <span className="dim">
                      {t('bt.samples', { count: buy.samples })}
                    </span>
                  </td>
                  <td>
                    {fmtPercent(sell?.win_rate, 0)}{' '}
                    <span className="dim">
                      {t('bt.samples', { count: sell?.samples ?? 0 })}
                    </span>
                  </td>
                  <td className="dim">{fmtPercent(base?.up_rate, 0)}</td>
                  <td className={direction(edge?.buy_edge)}>
                    {fmtPoints(edge?.buy_edge)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="dim" style={{ margin: '10px 0 0' }}>
            {t('bt.baselineNote')}
          </p>
        </>
      )}

      <ul className="reason-list">
        {sim.open_entry_date && (
          <li>{t('bt.openPosition', { date: sim.open_entry_date })}</li>
        )}
        {pending && <li>{t('bt.pendingNote')}</li>}
        {sim.average_holding_days !== null && sim.trade_count > 0 && (
          <li>
            {t('bt.holdingNote', {
              days: sim.average_holding_days.toFixed(1),
            })}
          </li>
        )}
        <li>
          {t('bt.coverage', {
            start: data.start,
            end: data.end,
            days: data.judged_days,
            signals: data.signal_count,
          })}
        </li>
      </ul>
    </section>
  )
}
