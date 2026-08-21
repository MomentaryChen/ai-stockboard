/**
 * The signed-out 自選股, kept in localStorage.
 *
 * Signed-in users keep theirs in the database instead (see useWatchlist), but
 * this stays the store for visitors who have not registered, and the source
 * that gets merged into an account on first sign-in.
 */

const STORAGE_KEY = 'ai-stockboard.watchlist'

/** Mirrors DEFAULT_SIDS in server/app/services/watchlist.py -- keep them in
 *  step so a signed-in and a signed-out board start out the same. */
export const DEFAULT_WATCHLIST = ['2330', '2317', '0050']

/** The server enforces the same cap (watchlist_service.MAX_ITEMS), and
 *  /api/realtime only quotes 20 codes per request anyway. */
export const MAX_WATCHLIST = 20

/** Mirrors watchlist_service.MAX_GROUPS -- folders on the same 20-stock list,
 *  not extra lists, so the chip row stays a chip row. */
export const MAX_WATCHLIST_GROUPS = 10

/** What is actually stored, or null when the visitor has never curated a list. */
export function readStoredWatchlist(): string[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) && parsed.length > 0 ? (parsed as string[]) : null
  } catch {
    return null
  }
}

export function loadWatchlist(): string[] {
  return readStoredWatchlist() ?? DEFAULT_WATCHLIST
}

export function saveWatchlist(sids: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sids))
  } catch {
    /* storage unavailable (Safari private mode) -- the list just won't persist */
  }
}

export function clearStoredWatchlist() {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* nothing to do */
  }
}
