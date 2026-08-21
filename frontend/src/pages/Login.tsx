import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import {
  ACCOUNT_LOCKED,
  ApiError,
  isPendingApproval,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import type { Translate } from '../i18n/types'
import { errorMessage } from '../utils/errors'

/**
 * Turn a sign-in failure into something worth reading.
 *
 * Three of these are not "you got it wrong" and must not look like it: an
 * account waiting for an administrator, an account locked after repeated
 * failures, and an address that has been trying too often. All three are
 * narrowed on the server's contract strings rather than displayed raw --
 * those are English by the CLAUDE.md rule, and these have translations.
 */
function signInError(
  error: unknown,
  t: Translate,
): { message: string; tone: 'error' | 'warn' } {
  if (isPendingApproval(error)) {
    return { message: t('login.pendingApproval'), tone: 'warn' }
  }

  if (error instanceof ApiError && error.status === 429) {
    const locked = error.message === ACCOUNT_LOCKED
    // Rounded up, so "59 seconds" never renders as "0 minutes".
    const minutes = error.retryAfter ? Math.ceil(error.retryAfter / 60) : null
    if (minutes === null) {
      return { message: t(locked ? 'login.locked' : 'login.throttled'), tone: 'warn' }
    }
    return {
      message: t(locked ? 'login.lockedIn' : 'login.throttledIn', { minutes }),
      tone: 'warn',
    }
  }

  return {
    message: t('login.failed', { message: errorMessage(error, t) }),
    tone: 'error',
  }
}

export default function Login() {
  const { status, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()

  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')

  // Where RequireAuth bounced us from, so signing in resumes the original page.
  const from = (location.state as { from?: string } | null)?.from ?? '/realtime'

  const submit = useMutation({
    mutationFn: () => login(identifier.trim(), password),
    onSuccess: () => navigate(from, { replace: true }),
  })

  if (status === 'authenticated') return <Navigate to={from} replace />

  return (
    <div className="auth-page">
      <section className="card auth-card">
        <h2 className="card-title">{t('login.title')}</h2>

        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault()
            submit.mutate()
          }}
        >
          <div className="field">
            <label htmlFor="identifier">{t('login.identifier')}</label>
            <input
              id="identifier"
              className="text-input"
              autoComplete="username"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="password">{t('login.password')}</label>
            <input
              id="password"
              className="text-input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>

          {submit.isError &&
            (() => {
              const { message, tone } = signInError(submit.error, t)
              return <div className={`banner banner-${tone}`}>{message}</div>
            })()}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending ? t('login.submitting') : t('login.submit')}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          {t('login.noAccount')}{' '}
          <Link to="/register">{t('login.registerLink')}</Link>
        </p>
      </section>
    </div>
  )
}
