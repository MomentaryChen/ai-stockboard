import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const { status, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

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
        <h2 className="card-title">登入</h2>

        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault()
            submit.mutate()
          }}
        >
          <div className="field">
            <label htmlFor="identifier">帳號或 Email</label>
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
            <label htmlFor="password">密碼</label>
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
              登入失敗：{(submit.error as Error).message}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending ? '登入中…' : '登入'}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          還沒有帳號？<Link to="/register">註冊一個</Link>
        </p>
      </section>
    </div>
  )
}
