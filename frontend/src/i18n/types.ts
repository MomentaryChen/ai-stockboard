import { zhTW } from './locales/zh-TW'

export const LOCALES = ['zh-TW', 'en'] as const

export type Locale = (typeof LOCALES)[number]

/** zh-TW is the catalogue's shape; every other locale must cover the same keys. */
export type MessageKey = keyof typeof zhTW
export type Messages = Record<MessageKey, string>

export type TParams = Record<string, string | number>

/** Translate one key, substituting `{name}` placeholders. */
export type Translate = (key: MessageKey, params?: TParams) => string

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value)
}

/**
 * Placeholder substitution. Deliberately minimal -- no plural rules, because
 * Chinese has none and the English strings that count things are written to
 * read acceptably at 1 as well.
 */
export function format(template: string, params?: TParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}
