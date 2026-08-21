/**
 * The long gradesheet: what accumulating and holding actually returned.
 *
 * Sits under the 存股 checklist, opposite `BacktestCard` in the rule-based
 * section, and deliberately shares no metric with it. That card reports hit
 * rate over 5/10/20 days against the base rate of the same days; asked of a
 * method whose instruction is "buy it and do nothing", there is nothing to
 * count. Two gradesheets, read side by side, is the comparison this lane
 * exists to make possible -- so the job here is to be legibly *different*
 * rather than superficially comparable.
 *
 * Three choices about prominence:
 *
 *   * **Price and total return sit together, always.** The whole claim of the
 *     method is that the payouts matter; showing only the total would hide
 *     whether they did, and showing only the price would answer a different
 *     question.
 *   * **填息 is a headline, not a footnote.** A dividend whose gap never
 *     closes is the holder's own capital handed back, and a 6% yield that
 *     never fills is worse than a 3% one that does. It is the one risk this
 *     method has that a yield figure cannot express.
 *   * **The optimism is stated on the card.** No tax, no fees, dividends
 *     reinvested at the ex-date close. A backtest that quietly flatters is
 *     worse than none, and the disclaimer is cheaper than the correction.
 */

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

import type { HoldBacktestResponse } from '../api/types'
import { useI18n } from '../i18n'
import { direction, fmtPrice, fmtSignedPercent, shortDate } from '../utils/format'

const TOTAL = '#4c8dff'
const PRICE = '#8a91a3'

/** The API reports these already in percent; the shared helper takes a
 *  fraction. Converting here keeps signs and digits identical to every other
 *  return on the page. */
const pct = (value: number | null) =>
  value === null ? '--' : fmtSignedPercent(value / 100)

export default function HoldBacktestCard({ data }: { data: HoldBacktestResponse }) {
  const { t } = useI18n()

  const curve = useMemo(
    () =>
      data.curve.map((point) => ({
        date: point.date,
        label: shortDate(point.date),
        // Plotted as return rather than as an index, same as the short card:
        // "+85%" is read directly, "1.85" has to be converted first.
        total: point.total - 100,
        price: point.price - 100,
      })),
    [data.curve],
  )

  const fill = data.fill

  return (
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('hbt.title')}
        </h2>
        <span className="tag">{t('hbt.window', { years: data.years })}</span>
      </div>

      {/* The headline: what holding through the payouts was worth on top of
          the price move. */}
      <p style={{ margin: '0 0 14px', lineHeight: 1.6 }}>
        <span
          className={`stat-value ${direction(data.total_return_pct)}`}
          style={{ fontSize: 18 }}
        >
          {t('hbt.headline', { total: pct(data.total_return_pct) })}
        </span>
        <br />
        <span className="dim">
          {t('hbt.headlineDetail', {
            price: pct(data.price_return_pct),
            dividend: pct(data.dividend_return_pct),
          })}
        </span>
      </p>

      <div className="stat-grid">
        <div>
          <div className="stat-label">{t('hbt.totalReturn')}</div>
          <div className={`stat-value ${direction(data.total_return_pct)}`}>
            {pct(data.total_return_pct)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hbt.priceReturn')}</div>
          <div className={`stat-value ${direction(data.price_return_pct)}`}>
            {pct(data.price_return_pct)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hbt.annualised')}</div>
          <div
            className={`stat-value ${direction(data.annualised_return_pct ?? 0)}`}
          >
            {pct(data.annualised_return_pct)}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hbt.yieldOnCost')}</div>
          <div className="stat-value">
            {data.yield_on_cost_pct === null
              ? '--'
              : `${fmtPrice(data.yield_on_cost_pct)}%`}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hbt.cashCollected')}</div>
          <div className="stat-value">{fmtPrice(data.cash_collected, 2)}</div>
        </div>
        <div>
          <div className="stat-label">{t('hbt.maxDrawdown')}</div>
          <div className="stat-value down">{pct(data.max_drawdown_pct)}</div>
        </div>
      </div>

      {/* The benchmark. Against the price leg only -- 加權指數 excludes
          dividends, and putting it beside the total would flatter every stock
          by roughly its own yield. Absent rather than zero when the index has
          no bars over this window. */}
      {data.index_return_pct !== null && (
        <p className="dim" style={{ margin: '12px 0 0' }}>
          {t(
            (data.excess_price_return_pp ?? 0) >= 0
              ? 'hbt.beatIndex'
              : 'hbt.trailIndex',
            {
              index: pct(data.index_return_pct),
              excess: pct(data.excess_price_return_pp),
            },
          )}
          <br />
          <span style={{ fontSize: 11 }}>{t('hbt.indexCaveat')}</span>
        </p>
      )}

      {curve.length > 1 && (
        <div style={{ height: 200, marginTop: 14 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={curve} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#262d3d" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#5d6478' }} minTickGap={40} />
              <YAxis
                tick={{ fontSize: 11, fill: '#5d6478' }}
                tickFormatter={(v: number) => `${Math.round(v)}%`}
                width={48}
              />
              <Tooltip
                contentStyle={{
                  background: '#161b26',
                  border: '1px solid #364155',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v: number, key: string) => [
                  `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`,
                  key === 'total' ? t('hbt.legendTotal') : t('hbt.legendPrice'),
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: 11 }}
                formatter={(key: string) =>
                  key === 'total' ? t('hbt.legendTotal') : t('hbt.legendPrice')
                }
              />
              <ReferenceLine y={0} stroke="#364155" />
              <Line type="monotone" dataKey="price" stroke={PRICE} dot={false} strokeWidth={1.5} />
              <Line type="monotone" dataKey="total" stroke={TOTAL} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 填息. The risk a yield figure cannot express. */}
      <span className="ai-block-title">{t('hbt.fillTitle')}</span>
      {fill.events === 0 ? (
        <p className="dim" style={{ margin: '6px 0 0' }}>
          {t('hbt.fillNone')}
        </p>
      ) : (
        <p className="dim" style={{ margin: '6px 0 0' }}>
          {fill.fill_rate_pct === null
            ? t('hbt.fillPendingOnly', { pending: fill.pending })
            : t('hbt.fillSummary', {
                rate: fmtPrice(fill.fill_rate_pct, 0),
                filled: fill.filled,
                events: fill.events - fill.pending,
                days: fill.median_days_to_fill ?? '--',
              })}
          {fill.pending > 0 && fill.fill_rate_pct !== null
            ? ` ${t('hbt.fillPending', { pending: fill.pending })}`
            : ''}
        </p>
      )}

      <p className="dim" style={{ margin: '12px 0 0', fontSize: 11 }}>
        {t('hbt.coverage', {
          start: data.start,
          end: data.end,
          years: data.years,
          bars: data.bars,
        })}
      </p>
      {data.stock_dividend_years > 0 && (
        <p className="dim" style={{ margin: '4px 0 0', fontSize: 11 }}>
          {t('hbt.stockDividendNote', { years: data.stock_dividend_years })}
        </p>
      )}
      {data.dividend_coverage === 'recent' && (
        <p className="dim" style={{ margin: '4px 0 0', fontSize: 11 }}>
          {t('hbt.coverageRecent')}
        </p>
      )}
      <p className="dim ai-disclaimer">{t('hbt.disclaimer')}</p>
    </section>
  )
}
