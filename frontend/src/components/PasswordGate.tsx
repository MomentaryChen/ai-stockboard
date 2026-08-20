import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/** Where a locked account is allowed to be. Anything else redirects here. */
const CHANGE_PASSWORD_PATH = '/change-password'

/**
 * Holds an account whose password an ADMIN reset on the change-password page.
 *
 * Wraps the whole route table rather than sitting inside RequireAuth, because
 * the pages that need blocking are mostly *public* ones -- the market views
 * take no token at all, and letting a locked user browse them would make the
 * restriction look like a bug rather than a required step.
 *
 * This is convenience, not security: the server answers 403 to everything but
 * `me` and the password change regardless of what the client renders.
 */
export default function PasswordGate({ children }: { children: ReactNode }) {
  const { mustChangePassword } = useAuth()
  const { pathname } = useLocation()

  if (mustChangePassword && pathname !== CHANGE_PASSWORD_PATH) {
    return <Navigate to={CHANGE_PASSWORD_PATH} replace />
  }
  return <>{children}</>
}
