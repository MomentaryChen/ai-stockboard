/**
 * One watchlist stock, as a board row.
 *
 * The card this replaces was ~760px tall, so a 1440px screen held four of a
 * twenty-stock watchlist. A row is one line, and everything the card showed is
 * still here -- one click down, in the expanded row, because 開高低收 and 五檔
 * answer "should I trade this" rather than "is anything happening", and only
 * the second question is being asked twenty times at once.
 */

import { Link } from 'react-router-dom'

import type { BestFourPointResult, RealtimeQuote, WatchlistGroup } from '../api/types'
import { usePriceFlash } from '../hooks/usePriceFlash'
import { useWatchlistDrag } from '../hooks/watchlistDrag'
import { useI18n } from '../i18n'
import { direction, fmtInt, fmtPrice, fmtSigned } from '../utils/format'
import { lastPrice } from '../utils/openIntel'
import { startSidDrag } from '../utils/watchlistGroups'
import AiVerdictSection from './AiVerdict'
import BfpChip, { BfpDot } from './BfpChip'
import QuoteDetail from './QuoteDetail'

/**
 * What counts as a full-width change bar.
 *
 * The daily limit is ±10%, but scaling to it would render a normal session as
 * a row of stubs -- most moves are under 1.5%. 5% is half the limit: big enough
 * that an ordinary day is legible, honest enough that a limit move still pegs
 * the bar and every row shares one scale.
 */
const BAR_FULL_PCT = 5

export interface BoardEntry {
  code: string
  /** Absent until a quote arrives -- the watchlist stores codes, not names. */
  name?: string
  quote?: RealtimeQuote
  bfp?: BestFourPointResult
  /** Why this code has no quote, when the upstream said. */
  error?: string
}

interface Props {
  entry: BoardEntry
  expanded: boolean
  onToggle: (code: string) => void
  onRemove?: (code: string) => void
  groups?: WatchlistGroup[]
  groupId?: number | null
  onAssign?: (code: string, groupId: number | null) => void
  bfpLoading?: boolean
  /** Quotes are still on their way in, so "no quote" is premature. */
  fetching?: boolean
}

export default function QuoteRow({
  entry,
  expanded,
  onToggle,
  onRemove,
  groups,
  groupId,
  onAssign,
  bfpLoading,
  fetching,
}: Props) {
  const { t } = useI18n()
  const drag = useWatchlistDrag()
  const { quote } = entry
  const last = quote ? lastPrice(quote) : null
  const dir = direction(quote?.change)
  const flash = usePriceFlash(last)
  const pct = quote?.change_percent ?? null

  const barWidth =
    pct === null ? 0 : Math.min(Math.abs(pct) / BAR_FULL_PCT, 1) * 100

  // Re-filing is a drag, not a form: the select below still works (and is what
  // a keyboard or a touch screen gets), but the common case -- "this one belongs
  // over there" -- should not cost an expand plus a dropdown.
  const draggable = Boolean(onAssign)
  const dragging = drag.code === entry.code

  return (
    <>
      <tr className={`quote-row${expanded ? ' expanded' : ''}${dragging ? ' dragging' : ''}`}>
        <td className="col-toggle">
          {/* The handle carries `draggable`, not the row.
              A draggable row means every cell starts a drag, so a code cannot
              be selected to copy and the grab cursor has to lie about where to
              take hold. One narrow grip tells the truth and leaves the rest of
              the row behaving like text. */}
          {draggable && (
            <span
              className="drag-grip"
              draggable
              role="button"
              tabIndex={-1}
              aria-label={t('board.groupDragHint')}
              title={t('board.groupDragHint')}
              onDragStart={(event) => {
                startSidDrag(
                  event,
                  entry.code,
                  entry.name ? `${entry.code} ${entry.name}` : entry.code,
                )
                drag.begin(entry.code, groupId ?? null)
              }}
              onDragEnd={() => drag.end()}
            >
              ⠿
            </span>
          )}
          <button
            type="button"
            className="btn-icon toggle"
            aria-expanded={expanded}
            title={expanded ? t('board.collapse') : t('board.expand')}
            onClick={() => onToggle(entry.code)}
          >
            {expanded ? '▾' : '▸'}
          </button>
        </td>

        <td className="col-sym">
          {/* A link is draggable by default and would hand the drop target a
              URL instead of the code. */}
          <Link to={`/stock/${entry.code}`} className="sym-link" draggable={false}>
            <span className="sym-code">{entry.code}</span>
            <span className="sym-name">{entry.name ?? ''}</span>
          </Link>
        </td>

        <td className={`col-num col-price ${dir}${flash ? ` flash-${flash}` : ''}`}>
          {last === null ? (
            <span className="dim">
              {entry.error ?? (fetching ? t('realtime.loading') : t('realtime.noQuote'))}
            </span>
          ) : (
            fmtPrice(last)
          )}
        </td>

        <td className={`col-num col-change ${dir}`}>{fmtSigned(quote?.change)}</td>

        <td className={`col-pct ${dir}`}>
          {/* The bar gets its own track rather than sitting behind the digits:
              every row's bar then starts from the same edge, which is what
              makes two rows comparable at a glance. The number is for the eye
              that has already stopped here. */}
          <span className="pct-cell">
            <span className="change-pct">
              {pct === null ? '--' : `${fmtSigned(pct)}%`}
            </span>
            <span className="change-track">
              <span className={`change-bar bar-${dir}`} style={{ width: `${barWidth}%` }} />
            </span>
          </span>
        </td>

        <td className="col-num col-vol">{fmtInt(quote?.accumulate_trade_volume)}</td>

        <td className="col-signal">
          <BfpDot result={entry.bfp} loading={bfpLoading} />
        </td>

        <td className="col-actions">
          {onRemove && (
            <button
              type="button"
              className="btn-icon remove"
              title={t('realtime.remove')}
              onClick={() => onRemove(entry.code)}
            >
              ×
            </button>
          )}
        </td>
      </tr>

      {expanded && (
        <tr className="quote-detail-row">
          <td colSpan={8}>
            <div className="quote-detail">
              <BfpChip result={entry.bfp} loading={bfpLoading} />
              {/* Straight under the rule verdict, above the numbers: the two
                  answers to "so what do I do with it" belong next to each
                  other. This used to be the last thing in the row, under 五檔
                  and the group picker, which is past where anyone scrolls in a
                  detail they opened to read one line of. Nothing about the
                  spend changes -- the panel still generates on the button. */}
              <AiVerdictSection sid={entry.code} />
              {quote ? (
                <QuoteDetail quote={quote} />
              ) : (
                <p className="dim" style={{ margin: 0 }}>
                  {entry.error ?? t('realtime.noQuote')}
                </p>
              )}
              {onAssign && groups && groups.length > 0 && (
                <label className="group-assign">
                  {t('board.groupAssign')}
                  <select
                    className="text-input group-assign-select"
                    value={groupId ?? ''}
                    onChange={(event) =>
                      onAssign(
                        entry.code,
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
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
