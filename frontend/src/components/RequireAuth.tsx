import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/**
 * Route guard. react-router is used declaratively here (no data router, so no
 * loaders), which makes a wrapper component the place for this.
 */
export default function RequireAuth({
  children,
  adminOnly = false,
}: {
  children: ReactNode
  adminOnly?: boolean
}) {
  const { status, isAdmin } = useAuth()
  const location = useLocation()

  // Rendering a placeholder rather than redirecting matters: on a hard refresh
  // of a guarded URL the session is still being restored, and redirecting here
  // would bounce a signed-in user to the login page.
  if (status === 'loading') {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  // Signed in but not permitted -- send them home, not to the login page.
  if (adminOnly && !isAdmin) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
