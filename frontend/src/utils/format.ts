/** Shared number/date formatting. Taiwan convention: red = up, green = down. */

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

/** TWSE reports 成交股數; traders think in 張 (1 張 = 1000 股). */
export function fmtLots(shares: number | null | undefined): string {
  if (shares === null || shares === undefined || Number.isNaN(shares)) return '--'
  return Math.round(shares / 1000).toLocaleString('en-US')
}

export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)} 億`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(1)} 萬`
  return value.toLocaleString('en-US')
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
