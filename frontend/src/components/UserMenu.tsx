import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import LanguageSwitcher from './LanguageSwitcher'

/**
 * Everything on the right-hand side of the topbar, behind one button.
 *
 * It used to be a flat row of links, which grew with every feature: an admin
 * signed in saw six buttons plus a language toggle, and the bar wrapped on a
 * laptop. A dropdown keeps the cost of the *next* entry at zero, and the
 * account-scoped actions (change password, sign out, language) belong together
 * anyway. Administration itself is one link now -- /admin is the hub.
 */
export default function UserMenu() {
  const { status, user, isAdmin, mustChangePassword, logout } = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    document.addEventListener('keydown', onEscape)
    return () => {
      document.removeEventListener('mousedown', onClickOutside)
      document.removeEventListener('keydown', onEscape)
    }
  }, [])

  // Every entry is a link, so navigating is the normal way out of the menu.
  useEffect(() => setOpen(false), [pathname])

  if (status === 'loading') return <span className="spinner" />

  const signedIn = status === 'authenticated'

  return (
    <div className="row" style={{ gap: 8 }}>
      {/* Signing in is the one action worth a permanent button: burying it in
          the menu costs a click on the path a first-time visitor is on. */}
      {!signedIn && (
        <Link to="/login" className="btn btn-sm">
          {t('menu.login')}
        </Link>
      )}
      <div className="menu" ref={boxRef}>
        <button
          type="button"
          className={`btn btn-sm menu-trigger ${open ? 'active' : ''}`}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {signedIn ? user?.username : t('menu.guest')}
          <span className="menu-caret" aria-hidden="true">
            ▾
          </span>
        </button>

        {open && (
          <div className="menu-panel" role="menu">
            {signedIn && (
              <div className="menu-head">
                <div className="menu-head-name">{user?.username}</div>
                <div className="menu-head-mail dim">{user?.email}</div>
              </div>
            )}

            {/* Hidden mid-reset: <PasswordGate> is already holding them on the
                change-password page, so an admin link would only be a dead end. */}
            {isAdmin && !mustChangePassword && (
              <Link to="/admin" className="menu-item" role="menuitem">
                {t('menu.admin')}
              </Link>
            )}
            {signedIn && !mustChangePassword && (
              <Link to="/change-password" className="menu-item" role="menuitem">
                {t('menu.changePassword')}
              </Link>
            )}

            <div className="menu-row">
              <span className="dim">{t('menu.language')}</span>
              <LanguageSwitcher />
            </div>

            <div className="menu-sep" />

            {signedIn ? (
              <button
                type="button"
                className="menu-item"
                role="menuitem"
                onClick={async () => {
                  setOpen(false)
                  await logout()
                  navigate('/')
                }}
              >
                {t('menu.logout')}
              </button>
            ) : (
              <Link to="/register" className="menu-item" role="menuitem">
                {t('menu.register')}
              </Link>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
