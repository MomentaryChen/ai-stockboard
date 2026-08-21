/**
 * Who is signed in.
 *
 * This is the only React context in the app. The "everything is local useState
 * plus react-query" rule holds everywhere else, but authentication is genuinely
 * cross-cutting: the topbar, the route guards and the watchlist all need it.
 *
 * The tokens themselves live in tokenStore (a plain module, because the API
 * layer needs them and is not a component); this subscribes to it so a token
 * cleared from anywhere -- a failed refresh, another browser tab -- lands here
 * as a re-render.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import { tokenStore } from '../api/tokenStore'
import type { User } from '../api/types'
import {
  DEFAULT_WATCHLIST,
  MAX_WATCHLIST,
  clearStoredWatchlist,
  readStoredWatchlist,
} from '../watchlistStorage'

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

interface AuthValue {
  status: AuthStatus
  user: User | null
  isAdmin: boolean
  /**
   * The account is holding a password an ADMIN generated for it. Every API
   * call but `me` and `changePassword` answers 403 until it is replaced, so
   * <PasswordGate> pins the user to /change-password while this is true.
   */
  mustChangePassword: boolean
  login: (identifier: string, password: string) => Promise<void>
  /**
   * Create an account. Resolves to `{ pending: true }` when the deployment
   * reviews registrations -- there is no session in that case, so the caller
   * must show the "waiting for an administrator" state rather than navigating
   * somewhere that requires being signed in.
   */
  register: (input: {
    username: string
    email: string
    password: string
    phone?: string
  }) => Promise<{ pending: boolean }>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

function isPristineDefault(sids: string[]): boolean {
  return (
    sids.length === DEFAULT_WATCHLIST.length &&
    sids.every((sid, index) => sid === DEFAULT_WATCHLIST[index])
  )
}

/**
 * Fold a watchlist curated while signed out into the account just signed in to.
 *
 * A union, so running it twice changes nothing -- which matters because
 * StrictMode invokes effects twice in development. The local copy is cleared
 * afterwards: without that, signing in as somebody else on the same browser
 * would pull the previous visitor's stocks into their account.
 */
async function mergeLocalWatchlist() {
  const local = readStoredWatchlist()
  if (!local || isPristineDefault(local)) {
    clearStoredWatchlist()
    return
  }

  try {
    const current = await api.getWatchlist()
    const merged = [
      ...current.sids,
      ...local.filter((sid) => !current.sids.includes(sid)),
    ].slice(0, MAX_WATCHLIST)

    if (merged.length !== current.sids.length) await api.putWatchlist(merged)
    clearStoredWatchlist()
  } catch {
    // Leave the local copy alone so the next sign-in can try the merge again.
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const accessToken = useSyncExternalStore(
    tokenStore.subscribe,
    tokenStore.getAccess,
  )

  const {
    data: user,
    isPending,
    isError,
  } = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    enabled: accessToken !== null,
    staleTime: Infinity,
    // The client already retries once behind a token refresh; retrying here on
    // top of that would just delay the fall back to anonymous.
    retry: false,
  })

  // On a cold load the stored access token is usually expired, so api.me() 401s,
  // the client refreshes once, and the retry succeeds -- all inside isPending.
  const status: AuthStatus =
    accessToken === null
      ? 'anonymous'
      : isPending
        ? 'loading'
        : isError || !user
          ? 'anonymous'
          : 'authenticated'

  const afterSignIn = useCallback(async () => {
    await mergeLocalWatchlist()
    await queryClient.invalidateQueries({ queryKey: ['watchlist'] })
  }, [queryClient])

  /**
   * Same as afterSignIn, but a no-op for an account that still has to change
   * its password: /api/watchlist answers 403 until it does, so running the
   * merge now would just fail. The local copy is left untouched and picked up
   * by changePassword() once the account is usable.
   */
  const afterSignInUnlessLocked = useCallback(
    async (signedIn: User) => {
      if (signedIn.must_change_password) return
      await afterSignIn()
    },
    [afterSignIn],
  )

  const login = useCallback(
    async (identifier: string, password: string) => {
      const tokens = await api.login(identifier, password)
      tokenStore.set(tokens.access_token, tokens.refresh_token)
      queryClient.setQueryData(['me'], tokens.user)
      await afterSignInUnlessLocked(tokens.user)
    },
    [afterSignInUnlessLocked, queryClient],
  )

  const register = useCallback(
    async (input: {
      username: string
      email: string
      password: string
      phone?: string
    }) => {
      const result = await api.register(input)
      // Awaiting approval: the account exists but has no session behind it, so
      // there is nothing to store and the locally kept watchlist stays where
      // it is -- it will be merged on the first real sign-in, once an admin
      // has let the account in.
      if (result.pending || !result.tokens) return { pending: true }

      const { tokens } = result
      tokenStore.set(tokens.access_token, tokens.refresh_token)
      queryClient.setQueryData(['me'], tokens.user)
      await afterSignInUnlessLocked(tokens.user)
      return { pending: false }
    },
    [afterSignInUnlessLocked, queryClient],
  )

  /**
   * Set a new password and adopt the session the server hands back.
   *
   * Storing the new tokens is not optional: the change revoked every refresh
   * token the account had, this one included, so keeping the old pair would
   * sign the user out the moment the access token expired.
   *
   * The user in that response is authoritative for `must_change_password` --
   * it only ever drops on the server, and trusting a local flip would unlock
   * the UI while every request still came back 403. The watchlist merge runs
   * here because this is the first moment it can succeed for an account that
   * signed in under a reset.
   */
  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const tokens = await api.changePassword(currentPassword, newPassword)
      tokenStore.set(tokens.access_token, tokens.refresh_token)
      queryClient.setQueryData(['me'], tokens.user)
      if (!tokens.user.must_change_password) await afterSignIn()
    },
    [afterSignIn, queryClient],
  )

  const logout = useCallback(async () => {
    const refreshToken = tokenStore.getRefresh()
    if (refreshToken) {
      // Best effort: the session is over locally either way.
      await api.logout(refreshToken).catch(() => undefined)
    }
    tokenStore.clear()

    // Only the per-user caches. queryClient.clear() would also evict the public
    // market data, and refetching a cold history costs ~18s behind the TWSE
    // rate limiter.
    queryClient.removeQueries({ queryKey: ['me'] })
    queryClient.removeQueries({ queryKey: ['watchlist'] })
  }, [queryClient])

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user: status === 'authenticated' ? (user ?? null) : null,
      isAdmin: status === 'authenticated' && user?.role === 'ADMIN',
      mustChangePassword:
        status === 'authenticated' && user?.must_change_password === true,
      login,
      register,
      changePassword,
      logout,
    }),
    [status, user, login, register, changePassword, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (value === null) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
