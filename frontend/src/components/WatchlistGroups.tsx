/**
 * Named folders on the watchlist, rendered as a segmented bar.
 *
 * Three zones rather than one wrapping run of chips: 全部/未分類 are pinned
 * left because they are the two the eye returns to, the user's own groups
 * scroll in the middle so ten of them cannot push the bar into four rows, and
 * the add button stays pinned right where it does not move as groups arrive.
 *
 * Filtering is a view property -- the quote poll still asks for every sid --
 * so switching groups never drops a request that was already paid for. Compact
 * (mini / floating) only switches; creating and renaming stay on the full board
 * where there is room to type.
 *
 * The chips are also the drop targets for a row dragged off the board, which is
 * how a stock gets re-filed without being filtered into view first. While a
 * drag is running the bar switches to drop mode: every bucket that would accept
 * the stock outlines itself, the one it already sits in says so instead of
 * pretending, and the 全部 filter -- which is not a folder -- greys out.
 */

import { useEffect, useRef, useState } from 'react'

import type { WatchlistGroup } from '../api/types'
import type { GroupFilter } from '../boardPrefs'
import { useGroupDrop, type GroupDropTarget } from '../hooks/useGroupDrop'
import { useI18n } from '../i18n'
import { MAX_WATCHLIST_GROUPS } from '../watchlistStorage'

interface Props {
  groups: WatchlistGroup[]
  groupBySid: Record<string, number>
  sids: string[]
  filter: GroupFilter
  onFilter: (filter: GroupFilter) => void
  onCreate: (name: string) => Promise<unknown>
  onRename: (groupId: number, name: string) => Promise<unknown>
  onDelete: (groupId: number) => Promise<unknown>
  /** Absent on the read-only variants, which makes the chips undroppable. */
  onAssign?: (code: string, groupId: number | null) => void
  compact?: boolean
}

function countIn(
  sids: string[],
  groupBySid: Record<string, number>,
  groupId: number | null,
): number {
  return sids.filter((sid) => (groupBySid[sid] ?? null) === groupId).length
}

export default function WatchlistGroups({
  groups,
  groupBySid,
  sids,
  filter,
  onFilter,
  onCreate,
  onRename,
  onDelete,
  onAssign,
  compact,
}: Props) {
  const { t } = useI18n()
  const [draft, setDraft] = useState<'create' | number | null>(null)
  const [name, setName] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const drop = useGroupDrop(groupBySid, onAssign)
  const dragging = drop.dragging !== null

  useEffect(() => {
    if (draft !== null) inputRef.current?.focus()
  }, [draft])

  const full = groups.length >= MAX_WATCHLIST_GROUPS
  const ungrouped = countIn(sids, groupBySid, null)

  async function submit() {
    const trimmed = name.trim()
    if (!trimmed) {
      setDraft(null)
      setName('')
      return
    }
    if (draft === 'create') await onCreate(trimmed)
    else if (typeof draft === 'number') await onRename(draft, trimmed)
    setDraft(null)
    setName('')
  }

  function startCreate() {
    if (full) return
    setDraft('create')
    setName('')
  }

  function startRename(group: WatchlistGroup) {
    setDraft(group.id)
    setName(group.name)
  }

  function nameInput(key: string, placeholder?: string, label?: string) {
    return (
      <input
        key={key}
        ref={inputRef}
        className="text-input group-chip-input"
        value={name}
        maxLength={20}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => setName(event.target.value)}
        onBlur={() => void submit()}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            void submit()
          }
          if (event.key === 'Escape') {
            setDraft(null)
            setName('')
          }
        }}
      />
    )
  }

  /**
   * The status word a bucket shows while a drag is running.
   *
   * Nothing is drawn otherwise, so the bar keeps its resting height and only
   * grows when there is something to say.
   */
  function hint(target: GroupDropTarget) {
    if (!dragging) return null
    if (target.current) return <span className="group-chip-hint">{t('board.groupHere')}</span>
    if (target.over) return <span className="group-chip-hint">{t('board.groupDropHere')}</span>
    if (target.droppable) {
      return <span className="group-chip-hint muted">{t('board.groupDroppable')}</span>
    }
    return null
  }

  const ungroupedTarget = drop.target(null)

  return (
    <div
      className={`group-bar${compact ? ' compact' : ''}${dragging ? ' dragging' : ''}`}
      role="group"
      aria-label={t('board.groupBarLabel')}
    >
      <div className="group-bar-fixed">
        <button
          type="button"
          className={`group-chip${filter.kind === 'all' ? ' active' : ''}${
            dragging ? ' drop-inert' : ''
          }`}
          title={dragging ? t('board.groupAllNotTarget') : undefined}
          onClick={() => onFilter({ kind: 'all' })}
        >
          <span className="group-chip-name">{t('board.groupAll')}</span>
          <span className="group-count">{sids.length}</span>
        </button>

        {groups.length > 0 && (
          <button
            type="button"
            className={`group-chip${filter.kind === 'ungrouped' ? ' active' : ''}${
              ungroupedTarget.className
            }`}
            onClick={() => onFilter({ kind: 'ungrouped' })}
            {...ungroupedTarget.handlers}
          >
            <span className="group-chip-name">{t('board.groupUngrouped')}</span>
            <span className="group-count">{ungrouped}</span>
            {hint(ungroupedTarget)}
          </button>
        )}
      </div>

      <div className="group-bar-scroll">
        {groups.length === 0 && !compact && draft !== 'create' && (
          <span className="group-bar-empty">{t('board.groupNoneYet')}</span>
        )}

        {groups.map((group) => {
          const selected = filter.kind === 'group' && filter.id === group.id
          if (draft === group.id) {
            return nameInput(String(group.id), undefined, t('board.groupRename'))
          }
          const target = drop.target(group.id)
          return (
            <span
              key={group.id}
              className={`group-chip-wrap${selected ? ' active' : ''}${target.className}`}
              {...target.handlers}
            >
              <button
                type="button"
                className="group-chip"
                onClick={() => onFilter({ kind: 'group', id: group.id })}
                onDoubleClick={() => {
                  if (!compact) startRename(group)
                }}
              >
                <span className="group-chip-name">{group.name}</span>
                <span className="group-count">{countIn(sids, groupBySid, group.id)}</span>
                {hint(target)}
              </button>
              {/* Hidden mid-drag: a small close button next to the bucket being
                  aimed at is a misfire waiting to happen, and a drag has nothing
                  to do with renaming or deleting. */}
              {!compact && selected && !dragging && (
                <>
                  <button
                    type="button"
                    className="btn-icon group-chip-edit"
                    title={t('board.groupRename')}
                    onClick={() => startRename(group)}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="btn-icon group-chip-edit"
                    title={t('board.groupDelete')}
                    onClick={() => {
                      if (window.confirm(t('board.groupDeleteConfirm', { name: group.name }))) {
                        void onDelete(group.id)
                      }
                    }}
                  >
                    ×
                  </button>
                </>
              )}
            </span>
          )
        })}
      </div>

      {!compact && (
        <div className="group-bar-tail">
          {draft === 'create' ? (
            nameInput('create', t('board.groupNamePlaceholder'), t('board.groupAdd'))
          ) : (
            <button
              type="button"
              className="group-chip group-chip-add"
              title={
                full
                  ? t('board.groupFull', { max: MAX_WATCHLIST_GROUPS })
                  : t('board.groupAddTitle')
              }
              disabled={full || dragging}
              onClick={startCreate}
            >
              {t('board.groupAdd')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
