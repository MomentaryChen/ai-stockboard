/**
 * The receiving half of drag-to-refile, shared by the chip row, the board and
 * the card grid.
 *
 * All three need the same decisions -- is this drag ours, which bucket is
 * hovered, is the drop a no-op, what does a real drop call -- and three copies
 * of that is three places for the no-op guard to fall out of step.
 *
 * The drag *itself* lives in WatchlistDragProvider rather than here, so every
 * bucket on the page learns about a pickup no matter which view it started in.
 * That is what lets a target advertise itself before the cursor arrives.
 */

import { useRef, useState, type DragEvent } from 'react'

import { useWatchlistDrag } from './watchlistDrag'
import { isSidDrag, readSidDrag, type GroupId } from '../utils/watchlistGroups'

export interface GroupDropTarget {
  /** Ready to concatenate onto a className. */
  className: string
  /** A drag is running and this bucket would accept it. */
  droppable: boolean
  /** A drag is running and the stock already lives here, so a drop would do
   *  nothing. Callers use it to say so rather than to hide the bucket -- an
   *  invisible current group makes the list jump mid-drag. */
  current: boolean
  /** The cursor is over this bucket right now. */
  over: boolean
  /** Spread onto the element that should accept the drop. Empty when there is
   *  no `onAssign`, which is what keeps the read-only variants inert. */
  handlers: {
    onDragEnter?: (event: DragEvent) => void
    onDragOver?: (event: DragEvent) => void
    onDragLeave?: (event: DragEvent) => void
    onDrop?: (event: DragEvent) => void
  }
}

function keyOf(id: GroupId): string {
  // `null` means ungrouped and still needs a key of its own -- it is a bucket,
  // not the absence of one.
  return id === null ? 'ungrouped' : `g${id}`
}

const INERT: GroupDropTarget = {
  className: '',
  droppable: false,
  current: false,
  over: false,
  handlers: {},
}

export function useGroupDrop(
  groupBySid: Record<string, number>,
  onAssign?: (code: string, groupId: number | null) => void,
) {
  const drag = useWatchlistDrag()
  const [over, setOver] = useState<string | null>(null)
  /**
   * How many nested elements of a target the cursor is currently inside.
   *
   * `dragenter`/`dragleave` fire for every child crossed, so leaving a chip for
   * its own count badge reads as a leave. The previous code silenced that with
   * `pointer-events: none` on all children, which also killed the chip's own
   * buttons mid-drag. Counting the crossings instead keeps the children live.
   */
  const depth = useRef(new Map<string, number>())

  function clear(key: string) {
    depth.current.set(key, 0)
    setOver((current) => (current === key ? null : current))
  }

  function target(id: GroupId): GroupDropTarget {
    if (!onAssign) return INERT
    const key = keyOf(id)
    const dragging = drag.code !== null
    // Read the live map first: the context snapshot is from pickup time, and a
    // drop that lands elsewhere mid-drag would leave it stale.
    const from = drag.code !== null ? (groupBySid[drag.code] ?? null) : null
    const current = dragging && from === id
    const droppable = dragging && !current
    const isOver = over === key && droppable

    const className = [
      droppable ? ' drop-target' : '',
      current ? ' drop-current' : '',
      isOver ? ' drop-over' : '',
    ].join('')

    return {
      className,
      droppable,
      current,
      over: isOver,
      handlers: {
        onDragEnter: (event) => {
          if (!isSidDrag(event)) return
          event.preventDefault()
          depth.current.set(key, (depth.current.get(key) ?? 0) + 1)
          setOver(key)
        },
        onDragOver: (event) => {
          if (!isSidDrag(event)) return
          // Without preventDefault the browser refuses the drop outright.
          event.preventDefault()
          // A no-op drop should say so with the cursor, not accept and discard.
          event.dataTransfer.dropEffect = current ? 'none' : 'move'
          // `dragenter` can be missed when a drag starts already inside the
          // target; dragover is the one event guaranteed to keep firing.
          if (over !== key) setOver(key)
        },
        onDragLeave: (event) => {
          if (!isSidDrag(event)) return
          const left = (depth.current.get(key) ?? 1) - 1
          depth.current.set(key, left)
          if (left <= 0) clear(key)
        },
        onDrop: (event) => {
          clear(key)
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

  return { dragging: drag.code, target }
}
