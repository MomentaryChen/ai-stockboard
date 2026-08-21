import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, isPasswordResetRequired, PASSWORD_RESET_REQUIRED } from './client'
import { tokenStore } from './tokenStore'
import type { TokenResponse, User } from './types'

const EXPIRED = 'expired-access'
const OLD_REFRESH = 'refresh-1'
const NEW_ACCESS = 'new-access'
const NEW_REFRESH = 'refresh-2'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function user(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    username: 'alice',
    email: 'alice@example.com',
    phone: null,
    role: 'USER',
    is_active: true,
    must_change_password: false,
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

function tokens(): TokenResponse {
  return {
    access_token: NEW_ACCESS,
    refresh_token: NEW_REFRESH,
    token_type: 'bearer',
    expires_in: 1800,
    user: user(),
  }
}

function authorization(init?: RequestInit): string | undefined {
  const headers = init?.headers
  if (!headers || headers instanceof Headers || Array.isArray(headers)) {
    return undefined
  }
  return (headers as Record<string, string>).Authorization
}

afterEach(() => {
  tokenStore.clear()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

beforeEach(() => {
  tokenStore.set(EXPIRED, OLD_REFRESH)
})

describe('refreshPromise single-flight', () => {
  it('issues one refresh when several authenticated calls 401 together', async () => {
    // The server rotates refresh tokens and treats a replay as stolen. Two
    // parallel refreshes would present the same token; the loser gets every
    // session dropped. See the comment on refreshPromise in client.ts.
    let refreshCalls = 0
    let releaseRefresh: (value: Response) => void = () => {}
    const refreshGate = new Promise<Response>((resolve) => {
      releaseRefresh = resolve
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === '/api/auth/refresh') {
          refreshCalls += 1
          const body = JSON.parse(String(init?.body)) as { refresh_token: string }
          expect(body.refresh_token).toBe(OLD_REFRESH)
          return refreshGate
        }
        if (url.startsWith('/api/realtime') || url === '/api/watchlist') {
          if (authorization(init) === `Bearer ${EXPIRED}`) {
            return jsonResponse({ detail: 'Invalid or expired token' }, 401)
          }
          if (url === '/api/watchlist') {
            return jsonResponse({ count: 0, sids: [] })
          }
          return jsonResponse({ success: true, message: null, quotes: [], errors: {} })
        }
        throw new Error(`unexpected fetch ${url}`)
      }),
    )

    const pending = [
      api.getRealtime(['2330']),
      api.getRealtime(['2317']),
      api.getWatchlist(),
    ]

    await vi.waitFor(() => {
      expect(refreshCalls).toBe(1)
    })

    releaseRefresh(jsonResponse(tokens()))
    await Promise.all(pending)

    expect(refreshCalls).toBe(1)
    expect(tokenStore.getAccess()).toBe(NEW_ACCESS)
    expect(tokenStore.getRefresh()).toBe(NEW_REFRESH)
  })

  it('retries the original request with the token written before awaiters resume', async () => {
    const seen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === '/api/auth/refresh') {
          return jsonResponse(tokens())
        }
        if (url.startsWith('/api/realtime')) {
          seen.push(authorization(init) ?? '')
          if (authorization(init) === `Bearer ${EXPIRED}`) {
            return jsonResponse({ detail: 'Invalid or expired token' }, 401)
          }
          return jsonResponse({ success: true, message: null, quotes: [], errors: {} })
        }
        throw new Error(`unexpected fetch ${url}`)
      }),
    )

    await api.getRealtime(['2330'])
    expect(seen).toEqual([`Bearer ${EXPIRED}`, `Bearer ${NEW_ACCESS}`])
  })

  it('clears the store when the refresh token is rejected, so the user is signed out', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/auth/refresh') {
          return jsonResponse({ detail: 'Invalid refresh token' }, 401)
        }
        if (url.startsWith('/api/realtime')) {
          return jsonResponse({ detail: 'Invalid or expired token' }, 401)
        }
        throw new Error(`unexpected fetch ${url}`)
      }),
    )

    await expect(api.getRealtime(['2330'])).rejects.toBeInstanceOf(ApiError)
    expect(tokenStore.getAccess()).toBeNull()
    expect(tokenStore.getRefresh()).toBeNull()
  })

  it('keeps the stored tokens when refresh fails with a network error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/auth/refresh') {
          throw new TypeError('network')
        }
        if (url.startsWith('/api/realtime')) {
          return jsonResponse({ detail: 'Invalid or expired token' }, 401)
        }
        throw new Error(`unexpected fetch ${url}`)
      }),
    )

    await expect(api.getRealtime(['2330'])).rejects.toBeInstanceOf(ApiError)
    expect(tokenStore.getAccess()).toBe(EXPIRED)
    expect(tokenStore.getRefresh()).toBe(OLD_REFRESH)
  })
})

describe('PASSWORD_RESET_REQUIRED', () => {
  it('matches the server contract string, and only that 403', () => {
    expect(PASSWORD_RESET_REQUIRED).toBe('Password reset required')
    expect(isPasswordResetRequired(new ApiError(PASSWORD_RESET_REQUIRED, 403))).toBe(true)
    expect(isPasswordResetRequired(new ApiError('Insufficient permissions', 403))).toBe(false)
    expect(isPasswordResetRequired(new ApiError(PASSWORD_RESET_REQUIRED, 401))).toBe(false)
  })
})
