/**
 * The language the UI is rendered in.
 *
 * Second context in the app after auth, and for the same reason: every screen
 * needs it. It sits *outside* AuthProvider in main.tsx so the sign-in page and
 * the loading spinner are already translated.
 *
 * `t` is rebuilt when the locale changes, which is what re-renders consumers --
 * no `key` prop on the tree, no remount, so charts and in-flight queries keep
 * their state across a language switch.
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

import { en } from './locales/en'
import { zhTW } from './locales/zh-TW'
import { readStoredLocale, saveLocale } from './storage'
import { format, type Locale, type Messages, type TParams, type Translate } from './types'

const CATALOGUES: Record<Locale, Messages> = {
  'zh-TW': zhTW,
  en,
}

/** What `toLocaleString` and friends should be handed for each UI language. */
const INTL_TAG: Record<Locale, string> = {
  'zh-TW': 'zh-TW',
  en: 'en-US',
}

interface I18nValue {
  locale: Locale
  /** BCP 47 tag for Intl APIs -- not always the same string as `locale`. */
  intlTag: string
  setLocale: (next: Locale) => void
  t: Translate
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale)

  // Keep the document in step: `lang` drives font fallback and screen readers,
  // and the tab title is the one string that lives outside React.
  useEffect(() => {
    document.documentElement.lang = locale
    document.title = CATALOGUES[locale]['app.title']
  }, [locale])

  const setLocale = useCallback((next: Locale) => {
    saveLocale(next)
    setLocaleState(next)
  }, [])

  const t = useCallback<Translate>(
    (key, params?: TParams) => format(CATALOGUES[locale][key], params),
    [locale],
  )

  const value = useMemo<I18nValue>(
    () => ({ locale, intlTag: INTL_TAG[locale], setLocale, t }),
    [locale, setLocale, t],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside <I18nProvider>')
  return value
}
