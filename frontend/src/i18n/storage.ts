/**
 * Where the chosen language lives: localStorage, on this browser only.
 *
 * There is no per-account language column on the server -- the preference is a
 * property of the device you are reading on, not of the account, and keeping it
 * client-side means it also applies before anyone signs in.
 */

import { isLocale, type Locale } from './types'

const STORAGE_KEY = 'ai-stockboard.locale'

/** Fallback when the visitor has never chosen: follow the browser, then zh-TW. */
export function detectLocale(): Locale {
  const candidates =
    typeof navigator === 'undefined'
      ? []
      : navigator.languages?.length
        ? navigator.languages
        : [navigator.language]

  for (const tag of candidates) {
    const lower = (tag ?? '').toLowerCase()
    if (lower.startsWith('zh')) return 'zh-TW'
    if (lower.startsWith('en')) return 'en'
  }
  // A Taiwan market board: Chinese is the better default for anyone else.
  return 'zh-TW'
}

export function readStoredLocale(): Locale {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (isLocale(raw)) return raw
  } catch {
    /* storage unavailable (Safari private mode) -- fall through to detection */
  }
  return detectLocale()
}

export function saveLocale(locale: Locale) {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    /* the choice just won't survive a reload */
  }
}
