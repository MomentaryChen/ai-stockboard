import { useI18n } from '../i18n'
import type { Locale, MessageKey } from '../i18n'

const OPTIONS: Array<{ locale: Locale; label: MessageKey; title: MessageKey }> = [
  { locale: 'zh-TW', label: 'lang.zh', title: 'lang.zhTitle' },
  { locale: 'en', label: 'lang.en', title: 'lang.enTitle' },
]

/**
 * Two-state language toggle in the topbar. A segmented control rather than a
 * <select> because with two locales the current one should be readable without
 * opening anything -- and each label is written in its own language, so both
 * stay legible whichever one is active.
 */
export default function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n()

  return (
    <div className="segmented">
      {OPTIONS.map((option) => (
        <button
          key={option.locale}
          type="button"
          className={`btn btn-sm ${locale === option.locale ? 'active' : ''}`}
          aria-pressed={locale === option.locale}
          title={t(option.title)}
          onClick={() => setLocale(option.locale)}
        >
          {t(option.label)}
        </button>
      ))}
    </div>
  )
}
