/**
 * One drag, known to every bucket on the page.
 *
 * Before this, each view held its own drag state: the chip row had one hook,
 * the board had another, the card grid a third. Picking up a board row told
 * only the board, so the chips -- the drop targets a user is most likely to
 * aim at -- could not know a drag was running and had nothing to light up.
 * "Where can I drop this?" was unanswerable by construction.
 *
 * Hoisting it here also fixes the aborted drag: `dragend` fires on the source
 * element only, so a drag cancelled with Escape left every *other* view's
 * highlight switched on with nothing to switch it off. The window listener
 * below is the one place that can see the end of a drag regardless of who
 * started it.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import type { GroupId } from '../utils/watchlistGroups'

export interface WatchlistDragValue {
  /** The sid in flight, or null when nothing is being dragged. */
  code: string | null
  /** The group that sid sits in right now, so a bucket can say "already here"
   *  instead of pretending to be a target that does nothing. */
  from: GroupId
  begin: (code: string, from: GroupId) => void
  end: () => void
}

const IDLE: WatchlistDragValue = {
  code: null,
  from: null,
  begin: () => {},
  end: () => {},
}

const WatchlistDragContext = createContext<WatchlistDragValue>(IDLE)

export function WatchlistDragProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ code: string | null; from: GroupId }>({
    code: null,
    from: null,
  })

  const end = useCallback(() => setState({ code: null, from: null }), [])
  const begin = useCallback(
    (code: string, from: GroupId) => setState({ code, from }),
    [],
  )

  useEffect(() => {
    if (state.code === null) return
    // `dragend` on window catches the source's own end, including Escape and a
    // drop onto nothing. `drop` covers the browsers that swallow `dragend`
    // when the drop lands outside the document.
    window.addEventListener('dragend', end)
    window.addEventListener('drop', end)
    return () => {
      window.removeEventListener('dragend', end)
      window.removeEventListener('drop', end)
    }
  }, [state.code, end])

  const value = useMemo<WatchlistDragValue>(
    () => ({ code: state.code, from: state.from, begin, end }),
    [state.code, state.from, begin, end],
  )

  return (
    <WatchlistDragContext.Provider value={value}>{children}</WatchlistDragContext.Provider>
  )
}

/** Falls back to an inert value, so a view rendered outside the provider still
 *  mounts -- it just never reports a drag. */
export function useWatchlistDrag(): WatchlistDragValue {
  return useContext(WatchlistDragContext)
}
