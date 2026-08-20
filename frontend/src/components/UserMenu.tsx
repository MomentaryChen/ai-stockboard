import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/** Sits in the topbar next to the health indicator. */
export default function UserMenu() {
  const { status, user, isAdmin, mustChangePassword, logout } = useAuth()
  const navigate = useNavigate()

  if (status === 'loading') return <span className="spinner" />

  if (status === 'anonymous') {
    return (
      <div className="row" style={{ gap: 8 }}>
        <Link to="/login" className="btn btn-sm">
          登入
        </Link>
        <Link to="/register" className="btn btn-sm">
          註冊
        </Link>
      </div>
    )
  }

  return (
    <div className="row" style={{ gap: 8 }}>
      {/* Hidden mid-reset: <PasswordGate> is already holding them on the
          change-password page, so an admin link would only be a dead end. */}
      {isAdmin && !mustChangePassword && (
        <>
          <Link to="/admin/users" className="btn btn-sm">
            使用者管理
          </Link>
          <Link to="/admin/stock-codes" className="btn btn-sm">
            名冊同步
          </Link>
        </>
      )}
      {!mustChangePassword && (
        <Link to="/change-password" className="btn btn-sm">
          變更密碼
        </Link>
      )}
      <span className="dim" title={user?.email}>
        {user?.username}
      </span>
      <button
        type="button"
        className="btn btn-sm"
        onClick={async () => {
          await logout()
          navigate('/')
        }}
      >
        登出
      </button>
    </div>
  )
}
