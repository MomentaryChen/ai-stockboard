/**
 * One watchlist stock, as a card.
 *
 * Still the right shape when the board holds three or four codes, or on a
 * phone where a table cannot keep six columns legible -- but no longer the
 * default: see WatchBoard for the dense view. The body below the headline is
 * QuoteDetail, shared with the expanded board row, so the two views cannot
 * drift apart.
 */

import { Link } from 'react-router-dom'

import type { BestFourPointResult, RealtimeQuote, WatchlistGroup } from '../api/types'
import { usePriceFlash } from '../hooks/usePriceFlash'
import { useI18n } from '../i18n'
import { direction, fmtPrice, fmtSigned } from '../utils/format'
import { lastPrice } from '../utils/openIntel'
import { startSidDrag } from '../utils/watchlistGroups'
import AiVerdictSection from './AiVerdict'
import BfpChip from './BfpChip'
import QuoteDetail from './QuoteDetail'

interface Props {
  quote: RealtimeQuote
  onRemove?: (code: string) => void
  groups?: WatchlistGroup[]
  groupId?: number | null
  onAssign?: (code: string, groupId: number | null) => void
  /** Set while this card is the one being dragged, so it can grey itself out. */
  dragging?: boolean
  onDragStateChange?: (code: string | null) => void
  bfp?: BestFourPointResult
  bfpLoading?: boolean
}

export default function RealtimeCard({
  quote,
  onRemove,
  groups,
  groupId,
  onAssign,
  dragging,
  onDragStateChange,
  bfp,
  bfpLoading,
}: Props) {
  const { t } = useI18n()
  const last = lastPrice(quote)
  const dir = direction(quote.change)
  const flash = usePriceFlash(last)

  // Same bargain as the board row: the select at the foot of the card still
  // works, but re-filing should not require finding it.
  const draggable = Boolean(onAssign)

  return (
    <article
      className={`card quote-card${draggable ? ' draggable' : ''}${dragging ? ' dragging' : ''}`}
      draggable={draggable}
      onDragStart={
        draggable
          ? (event) => {
              startSidDrag(event, quote.code)
              onDragStateChange?.(quote.code)
            }
          : undefined
      }
      onDragEnd={draggable ? () => onDragStateChange?.(null) : undefined}
    >
      {onRemove && (
        <button
          type="button"
          className="btn-icon remove"
          title={t('realtime.remove')}
          onClick={() => onRemove(quote.code)}
        >
          ×
        </button>
      )}

      <div className="row" style={{ gap: 10 }}>
        {/* A link is draggable by default and would hand the drop target a URL
            instead of the code. */}
        <Link
          to={`/stock/${quote.code}`}
          draggable={false}
          style={{ color: 'inherit', textDecoration: 'none', fontWeight: 700, fontSize: 17 }}
        >
          {quote.code} {quote.name}
        </Link>
      </div>

      <div className="row wrap" style={{ gap: 12, marginTop: 8 }}>
        <span className={`price-now ${dir}${flash ? ` flash-${flash}` : ''}`}>
          {fmtPrice(last)}
        </span>
        <span className={`price-change ${dir}`}>
          {fmtSigned(quote.change)}
          {quote.change_percent !== null ? ` (${fmtSigned(quote.change_percent)}%)` : ''}
        </span>
      </div>

      <BfpChip result={bfp} loading={bfpLoading} />

      {/* Same order as the expanded board row, and for the same reason: the
          two verdicts together, then the numbers they were drawn from. */}
      <AiVerdictSection sid={quote.code} />

      <div style={{ marginTop: 14 }}>
        <QuoteDetail quote={quote} />
      </div>

      {onAssign && groups && groups.length > 0 && (
        <label className="group-assign">
          {t('board.groupAssign')}
          <select
            className="text-input group-assign-select"
            value={groupId ?? ''}
            onChange={(event) =>
              onAssign(
                quote.code,
                event.target.value === '' ? null : Number(event.target.value),
              )
            }
          >
            <option value="">{t('board.groupNone')}</option>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </article>
  )
}
