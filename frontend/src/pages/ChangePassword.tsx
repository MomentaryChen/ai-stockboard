/**
 * Set a new password. Serves two arrivals with one form:
 *
 *   * voluntary -- reached from the topbar, nothing is blocked
 *   * forced    -- an ADMIN reset the password, `must_change_password` is set,
 *                  and <PasswordGate> will keep sending them back here until
 *                  they finish
 *
 * Both submit the same request, because the temporary password an admin
 * generated *is* the current password. Keeping one code path means the forced
 * case cannot quietly skip the "prove you know the current one" check.
 */

import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import {
  MIN_PASSWORD_LENGTH,
  passwordProblem,
  type PasswordProblem,
} from '../utils/password'

export default function ChangePassword() {
  const { status, mustChangePassword, changePassword } = useAuth()
  const navigate = useNavigate()
  const { t } = useI18n()

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<PasswordProblem | null>(null)

  const submit = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => navigate('/', { replace: true }),
  })

  // Nothing to change if there is no session. The gate never routes an
  // anonymous visitor here, but a bookmarked URL can.
  if (status === 'anonymous') return <Navigate to="/login" replace />
  if (status === 'loading') {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }

  return (
    <div className="auth-page">
      <section className="card auth-card">
        <h2 className="card-title">
          {mustChangePassword
            ? t('changePassword.titleForced')
            : t('changePassword.title')}
        </h2>

        {mustChangePassword && (
          <div className="banner banner-warn">{t('changePassword.forcedNotice')}</div>
        )}

        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault()
            const problem = passwordProblem(next, confirm)
            setLocalError(problem)
            if (!problem) submit.mutate()
          }}
        >
          <div className="field">
            <label htmlFor="current">
              {mustChangePassword
                ? t('changePassword.currentForced')
                : t('changePassword.current')}
            </label>
            <input
              id="current"
              className="text-input"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="next">{t('changePassword.next')}</label>
            <input
              id="next"
              className="text-input"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              required
            />
            <span className="field-hint">
              {t('register.passwordHint', { min: MIN_PASSWORD_LENGTH })}
            </span>
          </div>

          <div className="field">
            <label htmlFor="confirm">{t('changePassword.confirm')}</label>
            <input
              id="confirm"
              className="text-input"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              required
            />
          </div>

          {localError && (
            <div className="banner banner-error">
              {t(localError.key, localError.params)}
            </div>
          )}

          {submit.isError && (
            <div className="banner banner-error">
              {t('changePassword.failed', { message: (submit.error as Error).message })}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending
              ? t('changePassword.submitting')
              : t('changePassword.submit')}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          {t('changePassword.note')}
        </p>
      </section>
    </div>
  )
}
