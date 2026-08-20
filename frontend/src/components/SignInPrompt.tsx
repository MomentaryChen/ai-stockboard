import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

/**
 * The one place that asks an anonymous visitor to sign in.
 *
 * Deliberately not a redirect. RequireAuth bounces to /login because an admin
 * page has nothing to show without an account; 即時報價 sits next to charts and
 * analysis that anonymous visitors are welcome to, so throwing them out of the
 * page would read as a wall rather than an invitation.
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
  const from = { from: pathname }

  if (compact) {
    return (
      <span className="row wrap signin-inline">
        <span className="dim">{title}</span>
        <Link to="/login" state={from} className="btn btn-sm btn-primary">
          登入
        </Link>
        <Link to="/register" state={from} className="btn btn-sm">
          註冊
        </Link>
      </span>
    )
  }

  return (
    <section className="card signin-prompt">
      <span className="tag">需要登入</span>
      <h2 className="card-title">{title}</h2>
      {children}
      <div className="row wrap" style={{ gap: 10, marginTop: 18 }}>
        <Link to="/login" state={from} className="btn btn-primary">
          登入
        </Link>
        <Link to="/register" state={from} className="btn">
          註冊新帳號
        </Link>
      </div>
    </section>
  )
}
