import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { useAuth } from '../auth/AuthContext'

const MIN_PASSWORD_LENGTH = 8
// bcrypt hashes at most the first 72 bytes, and the server rejects anything
// longer rather than truncating it silently. A Chinese character is 3 bytes,
// so this is reachable with 25 of them -- check it here for a faster answer.
const MAX_PASSWORD_BYTES = 72

function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `密碼至少要 ${MIN_PASSWORD_LENGTH} 個字元`
  }
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
    return `密碼不可超過 ${MAX_PASSWORD_BYTES} 位元組（中文約 24 字）`
  }
  if (password !== confirm) return '兩次輸入的密碼不一致'
  return null
}

export default function Register() {
  const { status, register } = useAuth()
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

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
        <h2 className="card-title">註冊</h2>

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
            <label htmlFor="username">帳號</label>
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
            <span className="field-hint">3–32 個字元，不分大小寫</span>
          </div>

          <div className="field">
            <label htmlFor="email">Email</label>
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
            <label htmlFor="phone">手機（選填）</label>
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
            <label htmlFor="password">密碼</label>
            <input
              id="password"
              className="text-input"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <span className="field-hint">至少 {MIN_PASSWORD_LENGTH} 個字元</span>
          </div>

          <div className="field">
            <label htmlFor="confirm">確認密碼</label>
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

          {localError && <div className="banner banner-error">{localError}</div>}

          {submit.isError && (
            <div className="banner banner-error">
              註冊失敗：{(submit.error as Error).message}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending ? '註冊中…' : '註冊'}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          已經有帳號了？<Link to="/login">登入</Link>
        </p>
      </section>
    </div>
  )
}
