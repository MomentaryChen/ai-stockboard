/**
 * The 存股 checklist, as a card: five dimensions, a score, and what is missing.
 *
 * Free to render -- the endpoint behind it is arithmetic over stored rows -- so
 * unlike the AI cards this one fetches on mount, the same as 四大買賣點.
 *
 * Two things it must get right, both about honesty rather than layout.
 *
 * **An unknown dimension is drawn as unknown, not as a failure.** Every stock
 * on the board has no fundamentals until that ingest lands, so three of the
 * five chips are grey today. Rendering them as red crosses would tell the
 * reader the company failed checks nobody ran, and would make the card appear
 * to improve for reasons that have nothing to do with the company.
 *
 * **The evidence sentences are built here, not on the server.** Each dimension
 * arrives with `metrics` (numbers) and an English `evidence` line meant for the
 * prompt and the log. The card formats its own sentence from the numbers
 * through the message catalogue, so the English reader and the Chinese reader
 * get the same statement rather than one of them getting server prose.
 */

import type {
  ChenDimension,
  ChenRuleResult,
  ChenStatus,
  HoldFeatures,
  HoldSuitability,
} from '../api/types'
import { useI18n } from '../i18n'
import { GAP_KEY } from '../i18n/coverageGaps'
import type { MessageKey, TParams, Translate } from '../i18n'
import { fmtLots, fmtPrice } from '../utils/format'

const DIMENSION_KEY: Record<ChenDimension['key'], MessageKey> = {
  earn: 'hold.dimEarn',
  efficient: 'hold.dimEfficient',
  cheap: 'hold.dimCheap',
  collect: 'hold.dimCollect',
  liquid: 'hold.dimLiquid',
}

const STATUS_KEY: Record<ChenStatus, MessageKey> = {
  pass: 'hold.statusPass',
  fail: 'hold.statusFail',
  unknown: 'hold.statusUnknown',
}

export const SUITABILITY_KEY: Record<HoldSuitability, MessageKey> = {
  strong: 'hold.suitStrong',
  ok: 'hold.suitOk',
  weak: 'hold.suitWeak',
  avoid: 'hold.suitAvoid',
}

/** Reuses the 四大買賣點 palette rather than inventing a third colour scheme
 *  for the same screen. `weak` is deliberately neutral, not red: it means the
 *  case is not made, which is a different statement from `avoid`. */
const SUITABILITY_CLASS: Record<HoldSuitability, string> = {
  strong: 'signal-buy',
  ok: 'signal-buy',
  weak: 'signal-hold',
  avoid: 'signal-sell',
}

function num(metrics: Record<string, number>, key: string): number | null {
  return key in metrics ? metrics[key] : null
}

/** The sentence under a chip, composed from numbers rather than server prose. */
function evidence(dimension: ChenDimension, t: Translate): string {
  const m = dimension.metrics
  const unknown = dimension.status === 'unknown'
  const say = (key: MessageKey, params?: TParams) => t(key, params)

  switch (dimension.key) {
    case 'earn': {
      const years = num(m, 'years_checked') ?? 0
      const positive = num(m, 'positive_years')
      return unknown || positive === null
        ? say('hold.evidenceEarnUnknown', { years })
        : say('hold.evidenceEarn', { years, positive })
    }
    case 'efficient': {
      const years = num(m, 'years_checked') ?? 0
      const avg = num(m, 'avg_roe_pct')
      return unknown || avg === null
        ? say('hold.evidenceEfficientUnknown', { years })
        : say('hold.evidenceEfficient', { avg: fmtPrice(avg), years })
    }
    case 'cheap': {
      const pe = num(m, 'trailing_pe')
      const ceiling = num(m, 'ceiling')
      return unknown || pe === null || ceiling === null
        ? say('hold.evidenceCheapUnknown')
        : say('hold.evidenceCheap', { pe: fmtPrice(pe), ceiling: fmtPrice(ceiling) })
    }
    case 'collect': {
      if (unknown) return say('hold.evidenceCollectUnknown')
      const base = say('hold.evidenceCollect', {
        streak: num(m, 'consecutive_years') ?? 0,
        years: num(m, 'years_with_cash') ?? 0,
        // window_years lives on the features, but the chip only ever renders
        // beside them, so the caller's value is passed in via metrics.
        window: num(m, 'window_years') ?? 10,
      })
      const yieldPct = num(m, 'cash_yield_pct') ?? num(m, 'avg_yield_pct')
      return yieldPct === null
        ? base
        : base + say('hold.evidenceCollectYield', { yield: fmtPrice(yieldPct) })
    }
    case 'liquid': {
      const shares = num(m, 'avg_daily_shares')
      return unknown || shares === null
        ? say('hold.evidenceLiquidUnknown')
        : say('hold.evidenceLiquid', { lots: fmtLots(shares) })
    }
  }
}

function DimensionRow({ dimension }: { dimension: ChenDimension }) {
  const { t } = useI18n()
  return (
    <li className={`hold-dim hold-dim-${dimension.status}`}>
      <span className="hold-dim-mark" aria-hidden="true" />
      <span className="hold-dim-body">
        <span className="hold-dim-name">
          {t(DIMENSION_KEY[dimension.key])}
          <span className="hold-dim-status">{t(STATUS_KEY[dimension.status])}</span>
        </span>
        <span className="hold-dim-evidence">{evidence(dimension, t)}</span>
      </span>
    </li>
  )
}

export function HoldSuitabilityChip({
  suitability,
  compact = false,
}: {
  suitability: HoldSuitability
  compact?: boolean
}) {
  const { t } = useI18n()
  return (
    <div
      className={`signal ${compact ? 'signal-sm' : ''} ${SUITABILITY_CLASS[suitability]}`}
    >
      {t(SUITABILITY_KEY[suitability])}
    </div>
  )
}

export default function ChenHoldCard({
  features,
  rules,
}: {
  features: HoldFeatures
  rules: ChenRuleResult
}) {
  const { t } = useI18n()
  const { dividend, fundamentals, liquidity, price } = features

  // window_years is a property of the snapshot rather than of any one
  // dimension, so it is folded in here instead of being duplicated into the
  // server's metrics for every stock.
  const dimensions = rules.dimensions.map((d) =>
    d.key === 'collect'
      ? { ...d, metrics: { ...d.metrics, window_years: dividend.window_years } }
      : d,
  )

  return (
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('hold.title')}
        </h2>
        {rules.suitability ? (
          <HoldSuitabilityChip suitability={rules.suitability} compact />
        ) : (
          <span className="dim">{t('hold.unscored')}</span>
        )}
      </div>

      <div className="row-between wrap hold-score-row">
        <div>
          <div className="stat-label">{t('hold.score')}</div>
          <div className="hold-score">
            {rules.score === null ? '--' : t('hold.scoreOf', { score: rules.score })}
          </div>
        </div>
        <span className="dim">
          {t('hold.judgedOn', {
            known: rules.known_weight,
            total: rules.total_weight,
          })}
        </span>
      </div>

      <ul className="hold-dims">
        {dimensions.map((dimension) => (
          <DimensionRow key={dimension.key} dimension={dimension} />
        ))}
      </ul>

      <div className="stat-grid hold-stats">
        <div>
          <div className="stat-label">{t('hold.streak')}</div>
          <div className="stat-value">
            {t('hold.streakYears', { years: dividend.consecutive_years_with_cash })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hold.yield')}</div>
          <div className="stat-value">
            {dividend.cash_yield_pct === null
              ? '--'
              : `${fmtPrice(dividend.cash_yield_pct)}%`}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hold.pe')}</div>
          <div className="stat-value">{fmtPrice(fundamentals.trailing_pe)}</div>
        </div>
        <div>
          <div className="stat-label">{t('hold.roe')}</div>
          <div className="stat-value">
            {fundamentals.avg_roe_pct === null
              ? '--'
              : `${fmtPrice(fundamentals.avg_roe_pct)}%`}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hold.avgVolume')}</div>
          <div className="stat-value">
            {liquidity.avg_daily_shares === null
              ? '--'
              : t('hold.lots', { lots: fmtLots(liquidity.avg_daily_shares) })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('hold.position')}</div>
          <div className="stat-value">
            {price.position_pct === null ? '--' : `${fmtPrice(price.position_pct)}%`}
          </div>
        </div>
      </div>

      {/* Stock dividends dilute, and the cash yield above cannot show it. Only
          rendered when there were any, so a cash-only payer says nothing. */}
      {dividend.years_with_stock_dividend > 0 && (
        <p className="dim hold-note">
          {t('hold.stockDividend', {
            years: dividend.years_with_stock_dividend,
            window: dividend.window_years,
          })}
        </p>
      )}

      {rules.coverage_gaps.length > 0 && (
        <>
          <span className="ai-block-title">{t('hold.gaps')}</span>
          <ul className="reason-list coverage-gaps">
            {rules.coverage_gaps.map((gap) => (
              // An unrecognised key falls through as itself rather than
              // rendering blank -- same contract as serverText.ts.
              <li key={gap}>{gap in GAP_KEY ? t(GAP_KEY[gap]) : gap}</li>
            ))}
          </ul>
        </>
      )}

      <p className="dim ai-disclaimer">{t('hold.disclaimer')}</p>
    </section>
  )
}
