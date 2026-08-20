import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'

/** Sits in the topbar next to the health indicator. */
export default function UserMenu() {
  const { status, user, isAdmin, mustChangePassword, logout } = useAuth()
  const navigate = useNavigate()
  const { t } = useI18n()

  if (status === 'loading') return <span className="spinner" />

  if (status === 'anonymous') {
    return (
      <div className="row" style={{ gap: 8 }}>
        <Link to="/login" className="btn btn-sm">
          {t('menu.login')}
        </Link>
        <Link to="/register" className="btn btn-sm">
          {t('menu.register')}
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
            {t('menu.adminUsers')}
          </Link>
          <Link to="/admin/jobs" className="btn btn-sm">
            {t('menu.adminJobs')}
          </Link>
          <Link to="/admin/stock-codes" className="btn btn-sm">
            {t('menu.adminStockCodes')}
          </Link>
        </>
      )}
      {!mustChangePassword && (
        <Link to="/change-password" className="btn btn-sm">
          {t('menu.changePassword')}
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
        {t('menu.logout')}
      </button>
    </div>
  )
}
