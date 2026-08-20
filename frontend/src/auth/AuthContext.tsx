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
  login: (identifier: string, password: string) => Promise<void>
  register: (input: {
    username: string
    email: string
    password: string
    phone?: string
  }) => Promise<void>
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

  const login = useCallback(
    async (identifier: string, password: string) => {
      const tokens = await api.login(identifier, password)
      tokenStore.set(tokens.access_token, tokens.refresh_token)
      queryClient.setQueryData(['me'], tokens.user)
      await afterSignIn()
    },
    [afterSignIn, queryClient],
  )

  const register = useCallback(
    async (input: {
      username: string
      email: string
      password: string
      phone?: string
    }) => {
      const tokens = await api.register(input)
      tokenStore.set(tokens.access_token, tokens.refresh_token)
      queryClient.setQueryData(['me'], tokens.user)
      await afterSignIn()
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
      login,
      register,
      logout,
    }),
    [status, user, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (value === null) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
