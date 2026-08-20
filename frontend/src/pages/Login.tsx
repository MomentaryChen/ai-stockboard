import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'

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

          {submit.isError && (
            <div className="banner banner-error">
              {t('login.failed', { message: (submit.error as Error).message })}
            </div>
          )}

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
