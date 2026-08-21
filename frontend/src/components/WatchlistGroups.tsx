/**
 * Named folders on the watchlist, rendered as a chip row.
 *
 * Filtering is a view property -- the quote poll still asks for every sid --
 * so switching groups never drops a request that was already paid for. Compact
 * (mini / floating) only switches; creating and renaming stay on the full board
 * where there is room to type.
 *
 * The chips are also the drop targets for a row dragged off the board, which is
 * how a stock gets re-filed without being filtered into view first.
 */

import { useEffect, useRef, useState } from 'react'

import type { WatchlistGroup } from '../api/types'
import type { GroupFilter } from '../boardPrefs'
import { useGroupDrop } from '../hooks/useGroupDrop'
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

  return (
    <div className={`group-chips${compact ? ' compact' : ''}`}>
      <button
        type="button"
        className={`group-chip${filter.kind === 'all' ? ' active' : ''}`}
        onClick={() => onFilter({ kind: 'all' })}
      >
        {t('board.groupAll')}
        <span className="group-count">{sids.length}</span>
      </button>

      {groups.length > 0 && (
        <button
          type="button"
          className={`group-chip${filter.kind === 'ungrouped' ? ' active' : ''}${
            drop.target(null).className
          }`}
          onClick={() => onFilter({ kind: 'ungrouped' })}
          {...drop.target(null).handlers}
        >
          {t('board.groupUngrouped')}
          <span className="group-count">{ungrouped}</span>
        </button>
      )}

      {groups.map((group) => {
        const selected = filter.kind === 'group' && filter.id === group.id
        if (draft === group.id) {
          return (
            <input
              key={group.id}
              ref={inputRef}
              className="text-input group-chip-input"
              value={name}
              maxLength={20}
              aria-label={t('board.groupRename')}
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
        const target = drop.target(group.id)
        return (
          <span
            key={group.id}
            className={`group-chip-wrap${selected ? ' active' : ''}${target.className}`}
            {...target.handlers}
          >
            <button
              type="button"
              className={`group-chip${selected ? ' active' : ''}`}
              onClick={() => onFilter({ kind: 'group', id: group.id })}
              onDoubleClick={() => {
                if (!compact) startRename(group)
              }}
            >
              {group.name}
              <span className="group-count">{countIn(sids, groupBySid, group.id)}</span>
            </button>
            {!compact && selected && (
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

      {!compact && draft === 'create' && (
        <input
          ref={inputRef}
          className="text-input group-chip-input"
          value={name}
          maxLength={20}
          placeholder={t('board.groupNamePlaceholder')}
          aria-label={t('board.groupAdd')}
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
      )}

      {!compact && draft === null && (
        <button
          type="button"
          className="group-chip group-chip-add"
          title={full ? t('board.groupFull', { max: MAX_WATCHLIST_GROUPS }) : t('board.groupAddTitle')}
          disabled={full}
          onClick={startCreate}
        >
          {t('board.groupAdd')}
        </button>
      )}
    </div>
  )
}
