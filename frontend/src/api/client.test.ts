import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  ApiError,
  isPasswordResetRequired,
  isTimeout,
  PASSWORD_RESET_REQUIRED,
} from './client'
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
    // Required on User since the account-approval work; an approved, unlocked
    // account is the right default for a factory the auth tests build on.
    pending_approval: false,
    locked_until: null,
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

/** A fetch that never answers, but honours the abort signal the way a real one
 *  does -- which is the only thing that can distinguish a deadline from a hang. */
function neverAnswers() {
  return vi.fn(
    (_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      }),
  )
}

describe('request deadline', () => {
  it('gives up on a stalled request instead of spinning until nginx does', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', neverAnswers())

    const pending = api.getStock('2330').catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(20_000)

    const error = await pending
    expect(isTimeout(error)).toBe(true)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(408)
  })

  it('lets a cold-cache history fetch outlive the default budget', async () => {
    // ~18 s is a real first-fetch time for this route; the default would abort
    // work that was going to succeed, so it gets SLOW_TIMEOUT_MS instead.
    vi.useFakeTimers()
    vi.stubGlobal('fetch', neverAnswers())

    let settled = false
    const pending = api
      .getHistory('2330', 6)
      .catch((error: unknown) => error)
      .then((error) => {
        settled = true
        return error
      })

    // Well past DEFAULT_TIMEOUT_MS: if this route used it, the request would
    // already have been abandoned here.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(settled).toBe(false)

    await vi.advanceTimersByTimeAsync(35_000)
    expect(isTimeout(await pending)).toBe(true)
  })

  it('leaves a caller-cancelled request an AbortError', async () => {
    // StockSearch aborts on every keystroke. react-query treats AbortError as
    // "ignore this"; turning it into an ApiError would show the user an error
    // banner for typing.
    vi.stubGlobal('fetch', neverAnswers())

    const controller = new AbortController()
    const pending = api
      .searchStocks('233', 20, controller.signal)
      .catch((error: unknown) => error)
    controller.abort()

    const error = await pending
    expect((error as Error).name).toBe('AbortError')
    expect(isTimeout(error)).toBe(false)
  })
})

describe('error detail', () => {
  it('names the offending field for a 422 rather than rendering [object Object]', async () => {
    // FastAPI answers validation failures with a list of objects, not a string.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            detail: [
              {
                type: 'string_too_short',
                loc: ['body', 'password'],
                msg: 'String should have at least 8 characters',
              },
              {
                type: 'value_error',
                loc: ['body', 'email'],
                msg: 'value is not a valid email address',
              },
            ],
          },
          422,
        ),
      ),
    )

    const error = await api
      .register({ username: 'alice', email: 'nope', password: 'short' })
      .catch((e: unknown) => e)

    expect((error as ApiError).message).toBe(
      'password: String should have at least 8 characters; ' +
        'email: value is not a valid email address',
    )
  })

  it('passes a plain string detail through untouched', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'Username already taken' }, 409)),
    )

    const error = await api
      .register({ username: 'alice', email: 'a@example.com', password: 'longenough' })
      .catch((e: unknown) => e)

    expect((error as ApiError).message).toBe('Username already taken')
    expect((error as ApiError).status).toBe(409)
  })

  it('falls back to the status when there is no usable detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: [] }, 500)))

    const error = await api.getStock('2330').catch((e: unknown) => e)
    expect((error as ApiError).message).toBe('HTTP 500')
  })
})
