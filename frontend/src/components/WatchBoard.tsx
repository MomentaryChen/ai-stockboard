/**
 * The dense watchlist board: one row per stock, sortable, expandable.
 *
 * Sorting lives here rather than in the page because it is a property of the
 * view and must never be confused with the order of the watchlist itself --
 * `sort.key === 'watchlist'` is the identity sort, and it is the default, so
 * "the order I added them in" is always one click away.
 *
 * `grouped` splits the same rows under one heading per group instead of hiding
 * the ones that do not match. Showing everything and showing the folders are
 * not opposite requests, and the headings double as the drop targets that make
 * re-filing a drag rather than an expand-and-pick-from-a-select.
 */

import { useMemo, useState } from 'react'

import type { WatchlistGroup } from '../api/types'
import {
  readBoardSort,
  saveBoardSort,
  type BoardSort,
  type SortKey,
} from '../boardPrefs'
import { useGroupDrop } from '../hooks/useGroupDrop'
import { useI18n, type MessageKey } from '../i18n'
import { lastPrice } from '../utils/openIntel'
import { groupSections } from '../utils/watchlistGroups'
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

/** The toggle and the actions column bracket COLUMNS; a section heading spans
 *  the lot. */
const COLUMN_COUNT = COLUMNS.length + 2

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
  groups?: WatchlistGroup[]
  groupBySid?: Record<string, number>
  onAssign?: (code: string, groupId: number | null) => void
  /** Draw one section per group instead of one flat list. */
  grouped?: boolean
  bfpLoading?: boolean
  fetching?: boolean
  /** The floating/mini variants drop the columns they have no room for. */
  compact?: boolean
}


export default function WatchBoard({
  entries,
  onRemove,
  groups,
  groupBySid,
  onAssign,
  grouped,
  bfpLoading,
  fetching,
  compact,
}: Props) {
  const { t } = useI18n()
  const [sort, setSort] = useState<BoardSort>(readBoardSort)
  // Only one row at a time: two open details push the rest off screen, which is
  // the problem the board exists to solve.
  const [expanded, setExpanded] = useState<string | null>(null)
  const drop = useGroupDrop(groupBySid ?? {}, compact ? undefined : onAssign)

  const sorted = useMemo(() => sortEntries(entries, sort), [entries, sort])
  const sections = useMemo(
    () =>
      grouped
        ? groupSections(sorted, groups ?? [], groupBySid ?? {}, t('board.groupUngrouped'))
        : null,
    [grouped, sorted, groups, groupBySid, t],
  )

  function toggleSort(column: Column) {
    const key = column.key
    if (!key) return
    const next = nextSort(key, column.initialDir, sort)
    setSort(next)
    saveBoardSort(next)
  }

  function row(entry: BoardEntry) {
    return (
      <QuoteRow
        key={entry.code}
        entry={entry}
        expanded={expanded === entry.code}
        onToggle={(code) => setExpanded((open) => (open === code ? null : code))}
        onRemove={onRemove}
        groups={compact ? undefined : groups}
        groupId={groupBySid?.[entry.code] ?? null}
        onAssign={compact ? undefined : onAssign}
        bfpLoading={bfpLoading}
        fetching={fetching}
      />
    )
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
        {sections
          ? sections.map((section) => {
              const target = drop.target(section.id)
              return (
                <tbody
                  key={section.id ?? 'ungrouped'}
                  className={`group-section${target.className}`}
                  {...target.handlers}
                >
                  <tr className="group-section-row">
                    <td colSpan={COLUMN_COUNT}>
                      <span className="group-section-name">{section.name}</span>
                      <span className="group-count">{section.items.length}</span>
                      {/* Only while a drag is running, so a heading that is
                          just a heading stays one line. */}
                      {target.current && (
                        <span className="group-section-hint">{t('board.groupHere')}</span>
                      )}
                      {target.over && (
                        <span className="group-section-hint">{t('board.groupDropHere')}</span>
                      )}
                      {target.droppable && !target.over && (
                        <span className="group-section-hint muted">
                          {t('board.groupDroppable')}
                        </span>
                      )}
                    </td>
                  </tr>
                  {section.items.map(row)}
                  {section.items.length === 0 && (
                    <tr className="group-section-empty">
                      <td colSpan={COLUMN_COUNT}>{t('board.groupEmptyDrop')}</td>
                    </tr>
                  )}
                </tbody>
              )
            })
          : <tbody>{sorted.map(row)}</tbody>}
      </table>
    </div>
  )
}
