/**
 * How the watchlist board is laid out, on this browser.
 *
 * Same reasoning as the locale (see i18n/storage.ts): the choice belongs to the
 * screen you are reading on, not to the account. Somebody running the board on
 * a second monitor wants the dense list; the same account on a phone may want
 * the cards. Neither is worth a column on the server.
 */

export type BoardView = 'list' | 'card'

/** Sort is a property of the board, not of the watchlist -- reordering the view
 *  must never be mistaken for reordering the list that gets saved. */
export type SortKey = 'watchlist' | 'code' | 'price' | 'changePct' | 'volume'
export type SortDir = 'asc' | 'desc'

export interface BoardSort {
  key: SortKey
  dir: SortDir
}

const VIEW_KEY = 'ai-stockboard.boardView'
const SORT_KEY = 'ai-stockboard.boardSort'

export function readBoardView(): BoardView {
  try {
    const raw = localStorage.getItem(VIEW_KEY)
    if (raw === 'list' || raw === 'card') return raw
  } catch {
    /* storage unavailable (Safari private mode) -- fall through to the default */
  }
  // The list is the default because the board's job is 20 codes at a glance;
  // the cards are the detail view that used to be the only view.
  return 'list'
}

export function saveBoardView(view: BoardView) {
  try {
    localStorage.setItem(VIEW_KEY, view)
  } catch {
    /* the choice just won't survive a reload */
  }
}

const SORT_KEYS: SortKey[] = ['watchlist', 'code', 'price', 'changePct', 'volume']

export function readBoardSort(): BoardSort {
  try {
    const raw = localStorage.getItem(SORT_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<BoardSort>
      if (
        parsed.key &&
        SORT_KEYS.includes(parsed.key) &&
        (parsed.dir === 'asc' || parsed.dir === 'desc')
      ) {
        return { key: parsed.key, dir: parsed.dir }
      }
    }
  } catch {
    /* unreadable or hand-edited -- the default is always safe */
  }
  return { key: 'watchlist', dir: 'asc' }
}

export function saveBoardSort(sort: BoardSort) {
  try {
    localStorage.setItem(SORT_KEY, JSON.stringify(sort))
  } catch {
    /* the choice just won't survive a reload */
  }
}
