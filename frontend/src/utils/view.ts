/**
 * The chrome-free variant of a page, requested with `?view=mini`.
 *
 * It is a URL parameter rather than component state because the whole point is
 * that it survives being opened in its own window: `window.open` can only pass
 * a URL, and a bookmarked mini board has to come back mini.
 */

import { useLocation } from 'react-router-dom'

export const MINI_PARAM = 'view'
export const MINI_VALUE = 'mini'

/** Suggested popup size: tall and narrow, so it can sit beside real work. */
export const MINI_WINDOW = { width: 400, height: 620 }

export function useMiniView(): boolean {
  const { search } = useLocation()
  return new URLSearchParams(search).get(MINI_PARAM) === MINI_VALUE
}

/** The same path with the mini flag added -- used for the popup and its link. */
export function miniUrl(pathname: string): string {
  return `${pathname}?${MINI_PARAM}=${MINI_VALUE}`
}
