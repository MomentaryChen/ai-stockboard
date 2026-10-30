import { Link } from 'react-router-dom'

import type { EnginePlan, EngineRecommendation } from '../api/types'
import { useI18n, type MessageKey } from '../i18n'
import { translateEngineSector } from '../i18n/serverText'
import { fmtInt, fmtPrice } from '../utils/format'

/**
 * One stock answered in plain words: when to buy for a month, when to buy for a
 * quarter, and what to do if it is already held.
 *
 * The statistics stay out of this on purpose. Readers asked not to be shown
 * them, so every figure is an amount of money or a price. What the statistics
 * would have warned about is carried by the trust label instead of being dropped.
 */

//: The worked example. A round lot of a NT$2,000 stock is out of reach for many
//: readers, so this is a sum of money rather than a number of shares.
const EXAMPLE_AMOUNT = 100_000

const HOLD_COPY: Record<EngineRecommendation['hold_state'], { title: MessageKey; note: MessageKey; tone: string }> = {
  strong: { title: 'engine.holdStrong', note: 'engine.holdStrongNote', tone: 'go' },
  unclear: { title: 'engine.holdUnclear', note: 'engine.holdUnclearNote', tone: 'wait' },
  weak: { title: 'engine.holdWeak', note: 'engine.holdWeakNote', tone: 'wait' },
  unknown: { title: 'engine.holdUnknown', note: 'engine.holdUnknownNote', tone: 'no' },
}

function percent(share: number): string {
  return `${(share * 100).toFixed(1)}%`
}

function PlanBox({ label, plan }: { label: MessageKey; plan: EnginePlan | null }) {
  const { t } = useI18n()
  if (!plan) return null
  const entry = plan.entry
  const expected = entry ? entry.ev : plan.ev
  const amount = expected === null ? null : fmtInt(Math.round((Math.abs(expected) * EXAMPLE_AMOUNT) / 10) * 10)

  return (
    <div className="engine-box">
      <div className="dim">{t(label)}</div>
      {entry ? (
        entry.k_atr === 0 ? (
          <div className="engine-main go">{t('engine.buyNow')}</div>
        ) : (
          <div className="engine-main wait">{t('engine.buyAt', { price: fmtPrice(entry.price) })}</div>
        )
      ) : (
        <div className="engine-main no">{t('engine.noBuy')}</div>
      )}
      <div className="engine-note">
        {amount !== null &&
          t(expected !== null && expected >= 0 ? 'engine.earn' : 'engine.lose', {
            base: fmtInt(EXAMPLE_AMOUNT),
            amount,
          })}
        {entry && ` ${t('engine.maxWeight', { weight: percent(entry.max_weight) })}`}
        {!entry && ` ${t('engine.noBuyNote')}`}
      </div>
    </div>
  )
}

function HoldBox({ item }: { item: EngineRecommendation }) {
  const { t } = useI18n()
  const copy = HOLD_COPY[item.hold_state]
  return (
    <div className="engine-box">
      <div className="dim">{t('engine.holding')}</div>
      <div className={`engine-main ${copy.tone}`}>{t(copy.title)}</div>
      <div className="engine-note">{t(copy.note)}</div>
      {item.risk_line !== null && (
        <div className="engine-note">{t('engine.riskLine', { price: fmtPrice(item.risk_line) })}</div>
      )}
    </div>
  )
}

export default function EngineAdvice({
  item,
  linked = false,
}: {
  item: EngineRecommendation
  /** On the sector page the name leads to the stock; on the stock page it would lead nowhere. */
  linked?: boolean
}) {
  const { t } = useI18n()
  const title = `${item.name} ${item.sid}`

  return (
    <article className="card engine-card">
      <div className="engine-head">
        <strong>{linked ? <Link to={`/stock/${item.sid}`}>{title}</Link> : title}</strong>
        <span className="dim">
          {t('engine.sectorRank', {
            sector: translateEngineSector(item.sector, t),
            rank: item.sector_rank,
          })}
          {item.close !== null &&
            item.as_of &&
            ` · ${t('engine.closeOn', { price: fmtPrice(item.close), date: item.as_of })}`}
        </span>
      </div>
      {item.status === 'ok' ? (
        <>
          <div className="engine-grid">
            <PlanBox label="engine.short" plan={item.short} />
            <PlanBox label="engine.long" plan={item.long} />
            <HoldBox item={item} />
          </div>
          <p className="dim engine-foot">
            {item.long?.worst_5pct != null &&
              `${t('engine.badCase', { pct: percent(Math.abs(item.long.entry?.worst_5pct ?? item.long.worst_5pct)) })} `}
            {t('engine.trust', {
              short: t(item.short?.trust === 'medium' ? 'engine.trustMedium' : 'engine.trustLow'),
              long: t(item.long?.trust === 'medium' ? 'engine.trustMedium' : 'engine.trustLow'),
            })}
          </p>
        </>
      ) : (
        <p className="dim">{t('engine.unavailable')}</p>
      )}
    </article>
  )
}
