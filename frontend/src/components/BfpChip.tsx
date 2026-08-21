/**
 * 四大買賣點, at two densities.
 *
 * The chip carries the reasons and belongs in a detail view; the dot is what a
 * board row can afford. Splitting them is the point of the compact board: the
 * reasons are two lines of prose per stock, which is the right amount of text
 * when you are deciding about one stock and the wrong amount when you are
 * watching twenty.
 */

import type { BestFourPointResult } from '../api/types'
import { translateBfpLabel, translateBfpReason, useI18n } from '../i18n'

interface Props {
  result?: BestFourPointResult
  loading?: boolean
}

/** Compact 四大買賣點 chip with its reasons, for a card or an expanded row. */
export default function BfpChip({ result, loading }: Props) {
  const { t } = useI18n()

  if (loading && !result) {
    return (
      <div className="quote-bfp">
        <span className="dim" style={{ fontSize: 12 }}>
          {t('bfp.loading')}
        </span>
      </div>
    )
  }
  if (!result) return null

  return (
    <div className="quote-bfp">
      <div className={`signal signal-sm signal-${result.signal}`}>
        {translateBfpLabel(result.label, t)}
      </div>
      {result.reasons.length > 0 && (
        <ul className="quote-bfp-reasons">
          {result.reasons.map((reason) => (
            <li key={reason}>{translateBfpReason(reason, t)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * One row's worth of the same verdict: a coloured dot, with the label beside it
 * when the board is wide enough (the label is hidden by a container query, not
 * by a prop, because the deciding factor is the width of the board).
 */
export function BfpDot({ result, loading }: Props) {
  const { t } = useI18n()

  if (!result) {
    return <span className="sig-dot sig-none" title={loading ? t('bfp.loading') : undefined} />
  }

  const label = translateBfpLabel(result.label, t)
  return (
    <span className="sig-cell" title={label}>
      <span className={`sig-dot sig-${result.signal}`} />
      <span className="sig-label">{label}</span>
    </span>
  )
}
