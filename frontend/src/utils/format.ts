/** Shared number/date formatting. Taiwan convention: red = up, green = down. */

import type { Locale } from '../i18n'

export function fmtPrice(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toFixed(digits)
}

/** Index levels are five digits -- group them so 44,933.74 stays readable. */
export function fmtIndex(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toLocaleString('en-US')
}

/** TWSE reports 成交股數; traders think in 張 (1 張 = 1000 股, a "lot"). */
export function fmtLots(shares: number | null | undefined): string {
  if (shares === null || shares === undefined || Number.isNaN(shares)) return '--'
  return Math.round(shares / 1000).toLocaleString('en-US')
}

/**
 * Turnover, shortened.
 *
 * The two languages group large numbers differently and there is no way to
 * split the difference: Chinese counts in 萬 (10^4) and 億 (10^8), English in
 * K/M/B (10^3/10^6/10^9). Each gets its own scale rather than a literal
 * translation of the other's.
 */
export function fmtCompact(
  value: number | null | undefined,
  locale: Locale = 'zh-TW',
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  const abs = Math.abs(value)

  if (locale === 'en') {
    if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`
    if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`
    if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`
    return value.toLocaleString('en-US')
  }

  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 億`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(1)} 萬`
  return value.toLocaleString('en-US')
}

/** Volume-axis ticks, in 張/lots. Same scale problem as fmtCompact. */
export function fmtLotsAxis(value: number, locale: Locale = 'zh-TW'): string {
  if (locale === 'en') {
    if (value >= 1e6) return `${Math.round(value / 1e6)}M`
    if (value >= 1e4) return `${Math.round(value / 1e3)}K`
    // Keep a decimal below 10K, so a 2,500-lot tick does not read as "3K".
    if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`
    return `${Math.round(value)}`
  }

  // Keep a decimal below 10 萬 so a 25,000 張 tick does not read as "3 萬".
  if (value >= 100_000) return `${Math.round(value / 10_000)} 萬張`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)} 萬張`
  return `${Math.round(value)} 張`
}

export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

export type Direction = 'up' | 'down' | 'flat'

export function direction(value: number | null | undefined): Direction {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) {
    return 'flat'
  }
  return value > 0 ? 'up' : 'down'
}

/** '2026-08-20' -> '08/20' */
export function shortDate(iso: string): string {
  return iso.slice(5).replace('-', '/')
}
