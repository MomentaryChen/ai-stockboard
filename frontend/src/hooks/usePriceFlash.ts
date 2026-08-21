/**
 * A brief colour flash when a price changes.
 *
 * The board refreshes every POLL_MS and, without this, a tick is a digit
 * quietly swapping itself out -- invisible to anyone who is not staring
 * directly at that row, which is the whole point of a board you keep in the
 * corner of the screen. Motion is the only channel peripheral vision reads.
 *
 * The flash is driven off the *rendered* value rather than the poll, so a
 * refresh that returns the same print stays still: a board that blinks every
 * ten seconds regardless teaches the eye to ignore it.
 */

import { useEffect, useRef, useState } from 'react'

export type Flash = 'up' | 'down' | null

/** Taiwan convention: red = up, green = down -- the classes follow .up/.down. */
export function usePriceFlash(value: number | null | undefined, ms = 700): Flash {
  const previous = useRef(value)
  const [flash, setFlash] = useState<Flash>(null)

  useEffect(() => {
    const before = previous.current
    previous.current = value
    // First render has nothing to compare against, and a quote arriving after
    // a gap (null -> number) is not a tick.
    if (before === null || before === undefined || value === null || value === undefined) return
    if (value === before) return

    setFlash(value > before ? 'up' : 'down')
    const timer = setTimeout(() => setFlash(null), ms)
    return () => clearTimeout(timer)
  }, [value, ms])

  return flash
}
