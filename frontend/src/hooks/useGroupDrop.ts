/**
 * The receiving half of drag-to-refile, shared by the chip row, the board and
 * the card grid.
 *
 * All three need the same four decisions -- is this drag ours, which bucket is
 * hovered, is the drop a no-op, what does a real drop call -- and three copies
 * of that is three places for the no-op guard to fall out of step.
 */

import { useState, type DragEvent } from 'react'

import { isSidDrag, readSidDrag, type GroupId } from '../utils/watchlistGroups'

export interface GroupDropTarget {
  /** `''` or `' drop-over'`, ready to concatenate onto a className. */
  className: string
  /** Spread onto the element that should accept the drop. Empty when there is
   *  no `onAssign`, which is what keeps the read-only variants inert. */
  handlers: {
    onDragOver?: (event: DragEvent) => void
    onDragLeave?: () => void
    onDrop?: (event: DragEvent) => void
  }
}

function keyOf(id: GroupId): string {
  // `null` means ungrouped and still needs a key of its own -- it is a bucket,
  // not the absence of one.
  return id === null ? 'ungrouped' : `g${id}`
}

export function useGroupDrop(
  groupBySid: Record<string, number>,
  onAssign?: (code: string, groupId: number | null) => void,
) {
  const [over, setOver] = useState<string | null>(null)
  /** Which sid is in flight, so the source can grey itself out. */
  const [dragging, setDragging] = useState<string | null>(null)

  function target(id: GroupId): GroupDropTarget {
    if (!onAssign) return { className: '', handlers: {} }
    const key = keyOf(id)
    return {
      className: over === key ? ' drop-over' : '',
      handlers: {
        onDragOver: (event) => {
          if (!isSidDrag(event)) return
          // Without preventDefault the browser refuses the drop outright.
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
          setOver(key)
        },
        onDragLeave: () => setOver((current) => (current === key ? null : current)),
        onDrop: (event) => {
          setOver(null)
          if (!isSidDrag(event)) return
          event.preventDefault()
          const code = readSidDrag(event)
          // Dropping a stock back where it already was is a save the server
          // does not need to hear about.
          if (!code || (groupBySid[code] ?? null) === id) return
          onAssign(code, id)
        },
      },
    }
  }

  return { dragging, setDragging, target }
}
