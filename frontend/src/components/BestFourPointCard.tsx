import type { BestFourPointResult } from '../api/types'

const SIGNAL_HINT: Record<BestFourPointResult['signal'], string> = {
  buy: '符合買進條件',
  sell: '符合賣出條件',
  hold: '目前無明確訊號',
}

/** Verdict from the traditional (rule-based) analysis engine. */
export default function BestFourPointCard({
  result,
  asOf,
  sampleSize,
}: {
  result: BestFourPointResult
  asOf?: string
  sampleSize?: number
}) {
  return (
    <section className="card">
      <h2 className="card-title">四大買賣點</h2>

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
    </section>
  )
}
