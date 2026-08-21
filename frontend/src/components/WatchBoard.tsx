/**
 * The dense watchlist board: one row per stock, sortable, expandable.
 *
 * Sorting lives here rather than in the page because it is a property of the
 * view and must never be confused with the order of the watchlist itself --
 * `sort.key === 'watchlist'` is the identity sort, and it is the default, so
 * "the order I added them in" is always one click away.
 */

import { useMemo, useState } from 'react'

import {
  readBoardSort,
  saveBoardSort,
  type BoardSort,
  type SortKey,
} from '../boardPrefs'
import { useI18n, type MessageKey } from '../i18n'
import { lastPrice } from '../utils/openIntel'
import QuoteRow, { type BoardEntry } from './QuoteRow'

interface Column {
  key: SortKey | null
  label: MessageKey
  className: string
  /** Numbers read high-to-low first; text reads A-to-Z first. */
  initialDir: 'asc' | 'desc'
}

const COLUMNS: Column[] = [
  { key: 'code', label: 'board.colSymbol', className: 'col-sym', initialDir: 'asc' },
  { key: 'price', label: 'board.colPrice', className: 'col-num col-price', initialDir: 'desc' },
  { key: null, label: 'board.colChange', className: 'col-num col-change', initialDir: 'desc' },
  { key: 'changePct', label: 'board.colChangePct', className: 'col-pct', initialDir: 'desc' },
  { key: 'volume', label: 'board.colVolume', className: 'col-num col-vol', initialDir: 'desc' },
  { key: null, label: 'board.colSignal', className: 'col-signal', initialDir: 'desc' },
]

/** A missing number sorts last in either direction: "no quote" is not "zero". */
function compareNullable(a: number | null, b: number | null, dir: 'asc' | 'desc'): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return dir === 'asc' ? a - b : b - a
}

function sortEntries(entries: BoardEntry[], sort: BoardSort): BoardEntry[] {
  if (sort.key === 'watchlist') return entries

  const copy = [...entries]
  copy.sort((a, b) => {
    switch (sort.key) {
      case 'code':
        return sort.dir === 'asc'
          ? a.code.localeCompare(b.code)
          : b.code.localeCompare(a.code)
      case 'price':
        return compareNullable(
          a.quote ? lastPrice(a.quote) : null,
          b.quote ? lastPrice(b.quote) : null,
          sort.dir,
        )
      case 'changePct':
        return compareNullable(
          a.quote?.change_percent ?? null,
          b.quote?.change_percent ?? null,
          sort.dir,
        )
      case 'volume':
        return compareNullable(
          a.quote?.accumulate_trade_volume ?? null,
          b.quote?.accumulate_trade_volume ?? null,
          sort.dir,
        )
      default:
        return 0
    }
  })
  return copy
}

/**
 * Header click cycle: the column's natural direction, then its opposite, then
 * back to the watchlist's own order. Without the third step there is no way to
 * undo a sort, and the order the user curated is the one they will want back.
 */
function nextSort(key: SortKey, initialDir: 'asc' | 'desc', sort: BoardSort): BoardSort {
  if (sort.key !== key) return { key, dir: initialDir }
  if (sort.dir === initialDir) {
    return { key, dir: initialDir === 'asc' ? 'desc' : 'asc' }
  }
  return { key: 'watchlist', dir: 'asc' }
}

interface Props {
  entries: BoardEntry[]
  onRemove?: (code: string) => void
  bfpLoading?: boolean
  fetching?: boolean
  /** The floating/mini variants drop the columns they have no room for. */
  compact?: boolean
}

export default function WatchBoard({
  entries,
  onRemove,
  bfpLoading,
  fetching,
  compact,
}: Props) {
  const { t } = useI18n()
  const [sort, setSort] = useState<BoardSort>(readBoardSort)
  // Only one row at a time: two open details push the rest off screen, which is
  // the problem the board exists to solve.
  const [expanded, setExpanded] = useState<string | null>(null)

  const sorted = useMemo(() => sortEntries(entries, sort), [entries, sort])

  function toggleSort(column: Column) {
    const key = column.key
    if (!key) return
    const next = nextSort(key, column.initialDir, sort)
    setSort(next)
    saveBoardSort(next)
  }

  return (
    <div className={`watch-board${compact ? ' compact' : ''}`}>
      <table className="quote-table">
        <thead>
          <tr>
            <th className="col-toggle" />
            {COLUMNS.map((column) => {
              const active = column.key !== null && sort.key === column.key
              return (
                <th key={column.label} className={column.className}>
                  {column.key ? (
                    <button
                      type="button"
                      className={`th-sort${active ? ' active' : ''}`}
                      onClick={() => toggleSort(column)}
                      title={t('board.sortBy', { column: t(column.label) })}
                    >
                      {t(column.label)}
                      {active && <span className="sort-caret">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
                    </button>
                  ) : (
                    t(column.label)
                  )}
                </th>
              )
            })}
            <th className="col-actions" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <QuoteRow
              key={entry.code}
              entry={entry}
              expanded={expanded === entry.code}
              onToggle={(code) => setExpanded((open) => (open === code ? null : code))}
              onRemove={onRemove}
              bfpLoading={bfpLoading}
              fetching={fetching}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
