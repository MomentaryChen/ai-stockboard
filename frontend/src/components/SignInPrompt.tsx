import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { useI18n } from '../i18n'

/**
 * The one place that asks an anonymous visitor to sign in.
 *
 * Deliberately not a redirect. RequireAuth bounces to /login because an admin
 * page has nothing to show without an account; the realtime board sits next to
 * charts and analysis that anonymous visitors are welcome to, so throwing them
 * out of the page would read as a wall rather than an invitation.
 *
 * Both links carry the current path, which is what Login/Register read back as
 * `from` -- so signing in or registering lands the visitor exactly where they
 * were, with the quotes already loading.
 */
export default function SignInPrompt({
  title,
  children,
  compact = false,
}: {
  title: string
  children?: ReactNode
  /** Inline variant for a corner of an existing card, rather than a page block. */
  compact?: boolean
}) {
  const { pathname } = useLocation()
  const { t } = useI18n()
  const from = { from: pathname }

  if (compact) {
    return (
      <span className="row wrap signin-inline">
        <span className="dim">{title}</span>
        <Link to="/login" state={from} className="btn btn-sm btn-primary">
          {t('menu.login')}
        </Link>
        <Link to="/register" state={from} className="btn btn-sm">
          {t('menu.register')}
        </Link>
      </span>
    )
  }

  return (
    <section className="card signin-prompt">
      <span className="tag">{t('signIn.badge')}</span>
      <h2 className="card-title">{title}</h2>
      {children}
      <div className="row wrap" style={{ gap: 10, marginTop: 18 }}>
        <Link to="/login" state={from} className="btn btn-primary">
          {t('menu.login')}
        </Link>
        <Link to="/register" state={from} className="btn">
          {t('signIn.registerNew')}
        </Link>
      </div>
    </section>
  )
}
