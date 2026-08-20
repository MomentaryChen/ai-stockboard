import type { BestFourPointResult, RuleSet } from '../api/types'
import {
  translateBfpLabel,
  translateBfpReason,
  useI18n,
  type MessageKey,
} from '../i18n'

const SIGNAL_HINT: Record<BestFourPointResult['signal'], MessageKey> = {
  buy: 'bfp.hintBuy',
  sell: 'bfp.hintSell',
  hold: 'bfp.hintHold',
}

const RULE_SETS: Array<{ key: RuleSet; label: MessageKey; title: MessageKey }> = [
  { key: 'grs', label: 'bfp.ruleGrs', title: 'bfp.ruleGrsTitle' },
  { key: 'twstock', label: 'bfp.ruleTwstock', title: 'bfp.ruleTwstockTitle' },
]

/** Verdict from the traditional (rule-based) analysis engine.
 *
 *  The rule-set switch is deliberately on the card rather than the page: the
 *  two variants disagree often enough that the verdict is meaningless without
 *  showing which one produced it. */
export default function BestFourPointCard({
  result,
  asOf,
  sampleSize,
  ruleSet,
  onRuleSetChange,
}: {
  result: BestFourPointResult
  asOf?: string
  sampleSize?: number
  ruleSet?: RuleSet
  onRuleSetChange?: (next: RuleSet) => void
}) {
  const { t } = useI18n()

  const holdWhy =
    result.signal === 'hold' && result.label === "Don't touch" && result.reasons.length > 0

  return (
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('bfp.title')}
        </h2>

        {ruleSet && onRuleSetChange && (
          <div className="segmented">
            {RULE_SETS.map(({ key, label, title }) => (
              <button
                key={key}
                type="button"
                title={t(title)}
                className={`btn btn-sm ${ruleSet === key ? 'active' : ''}`}
                onClick={() => onRuleSetChange(key)}
              >
                {t(label)}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 'Buy' / 'Sell' / "Don't touch" already arrive in English; only the
          not-enough-data verdict is Chinese on the wire. */}
      <div className={`signal signal-${result.signal}`}>
        {translateBfpLabel(result.label, t)}
      </div>

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {holdWhy ? t('bfp.hintHoldThreshold') : t(SIGNAL_HINT[result.signal])}
        {asOf ? ` · ${t('bfp.asOf', { date: asOf })}` : ''}
        {sampleSize ? ` · ${t('bfp.sampleSize', { count: sampleSize })}` : ''}
      </p>

      {result.reasons.length > 0 && (
        <ul className="reason-list">
          {result.reasons.map((reason) => (
            <li key={reason}>{translateBfpReason(reason, t)}</li>
          ))}
        </ul>
      )}

      {ruleSet === 'twstock' && (
        <p className="dim" style={{ margin: '10px 0 0' }}>
          {t('bfp.twstockWarning')}
        </p>
      )}
    </section>
  )
}
