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
import { MIN_PASSWORD_LENGTH, passwordProblem } from '../utils/password'

export default function ChangePassword() {
  const { status, mustChangePassword, changePassword } = useAuth()
  const navigate = useNavigate()

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

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
        <h2 className="card-title">{mustChangePassword ? '請設定新密碼' : '變更密碼'}</h2>

        {mustChangePassword && (
          <div className="banner banner-warn">
            管理員已重設你的密碼。在你設定自己的新密碼之前，其他功能都無法使用。
          </div>
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
              {mustChangePassword ? '管理員給的臨時密碼' : '目前密碼'}
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
            <label htmlFor="next">新密碼</label>
            <input
              id="next"
              className="text-input"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
              required
            />
            <span className="field-hint">至少 {MIN_PASSWORD_LENGTH} 個字元</span>
          </div>

          <div className="field">
            <label htmlFor="confirm">確認新密碼</label>
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
              變更失敗：{(submit.error as Error).message}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submit.isPending}
          >
            {submit.isPending ? '變更中…' : '設定新密碼'}
          </button>
        </form>

        <p className="dim" style={{ marginTop: 14 }}>
          變更後其他裝置上的登入狀態都會失效，這台會留著。
        </p>
      </section>
    </div>
  )
}
