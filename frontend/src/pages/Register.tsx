import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import {
  MIN_PASSWORD_LENGTH,
  passwordProblem,
  type PasswordProblem,
} from '../utils/password'

export default function Register() {
  const { status, register } = useAuth()
  const navigate = useNavigate()
  const { t } = useI18n()

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  // Held as a key, not a sentence, so switching language re-renders it.
  const [localError, setLocalError] = useState<PasswordProblem | null>(null)

  const submit = useMutation({
    mutationFn: () =>
      register({
        username: username.trim(),
        email: email.trim(),
        password,
        phone: phone.trim() || undefined,
      }),
    onSuccess: () => navigate('/realtime', { replace: true }),
  })

  if (status === 'authenticated') return <Navigate to="/realtime" replace />

  return (
    <div className="auth-page">
      <section className="card auth-card">
        <h2 className="card-title">{t('register.title')}</h2>

        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault()
            const problem = passwordProblem(password, confirm)
            setLocalError(problem)
            if (!problem) submit.mutate()
          }}
        >
          <div className="field">
            <label htmlFor="username">{t('register.username')}</label>
            <input
              id="username"
              className="text-input"
              autoComplete="username"
              minLength={3}
              maxLength={32}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
            <span className="field-hint">{t('register.usernameHint')}</span>
          </div>

          <div className="field">
            <label htmlFor="email">{t('register.email')}</label>
            <input
              id="email"
              className="text-input"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="phone">{t('register.phone')}</label>
            <input
              id="phone"
              className="text-input"
              type="tel"
              autoComplete="tel"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="password">{t('register.password')}</label>
            <input
              id="password"
              className="text-input"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <span className="field-hint">
              {t('register.passwordHint', { min: MIN_PASSWORD_LENGTH })}
            </span>
          </div>

          <div className="field">
            <label htmlFor="confirm">{t('register.confirm')}</label>
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
              {t('register.failed', { message: (submit.error as Error).message })}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending ? t('register.submitting') : t('register.submit')}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          {t('register.haveAccount')}{' '}
          <Link to="/login">{t('register.loginLink')}</Link>
        </p>
      </section>
    </div>
  )
}
