import type { BestFourPointResult, RuleSet } from '../api/types'

const SIGNAL_HINT: Record<BestFourPointResult['signal'], string> = {
  buy: '符合買進條件',
  sell: '符合賣出條件',
  hold: '目前無明確訊號',
}

const RULE_SETS: Array<{ key: RuleSet; label: string; title: string }> = [
  {
    key: 'grs',
    label: '修正版',
    title: '四大買賣點的參考行為：乖離轉折關卡生效，「量縮價不跌／價跌」與昨收比較',
  },
  {
    key: 'twstock',
    label: 'twstock',
    title: 'twstock 1.5.1 原樣：乖離關卡失效，「量縮價不跌／價跌」誤與昨開比較',
  },
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
  return (
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          四大買賣點
        </h2>

        {ruleSet && onRuleSetChange && (
          <div className="segmented">
            {RULE_SETS.map(({ key, label, title }) => (
              <button
                key={key}
                type="button"
                title={title}
                className={`btn btn-sm ${ruleSet === key ? 'active' : ''}`}
                onClick={() => onRuleSetChange(key)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className={`signal signal-${result.signal}`}>{result.label}</div>

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {SIGNAL_HINT[result.signal]}
        {asOf ? ` · 資料截至 ${asOf}` : ''}
        {sampleSize ? ` · ${sampleSize} 個交易日` : ''}
      </p>

      {result.reasons.length > 0 && (
        <ul className="reason-list">
          {result.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {ruleSet === 'twstock' && (
        <p className="dim" style={{ margin: '10px 0 0' }}>
          twstock 1.5.1 的移植缺陷讓乖離轉折關卡失效，訊號會明顯偏多，僅供對照。
        </p>
      )}
    </section>
  )
}
