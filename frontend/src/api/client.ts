import { tokenStore } from './tokenStore'
import type {
  Job,
  JobListResponse,
  JobRunsResponse,
  JobScheduleUpdate,
  JobTriggerResponse,
  PasswordResetResponse,
  Role,
  RuleSet,
  TokenResponse,
  TraditionalAnalysisResponse,
  HealthResponse,
  HistoryResponse,
  DividendResponse,
  RealtimeResponse,
  SearchResponse,
  StockInfo,
  User,
  UserListResponse,
  WatchlistResponse,
} from './types'

/** An HTTP failure that still carries its status code.
 *
 *  Extends Error so the existing `(error as Error).message` call sites keep
 *  working, while auth code can narrow on
 *  `err instanceof ApiError && err.status === 409`.
 */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The 403 detail the server sends while `must_change_password` is set.
 *
 *  Matches deps.PASSWORD_RESET_REQUIRED on the server; changing one without
 *  the other silently turns the redirect into a dead end.
 */
export const PASSWORD_RESET_REQUIRED = 'Password reset required'

/** True for the 403 that means "set a new password", not "you may not do this". */
export function isPasswordResetRequired(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    error.message === PASSWORD_RESET_REQUIRED
  )
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  /** Send the access token, and retry once after a refresh on 401. */
  auth?: boolean
}

function send(path: string, options: RequestOptions): Promise<Response> {
  const { method = 'GET', body, signal, auth = false } = options
  const headers: Record<string, string> = {}

  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = tokenStore.getAccess()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  return fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
}

// One in-flight refresh for the whole app. This is not an optimisation: the
// server rotates refresh tokens and treats a replayed one as stolen by dropping
// every session. Several authenticated queries expiring together would
// otherwise fire parallel refreshes, and all but one would present a token the
// server had just revoked -- signing the user out at random.
let refreshPromise: Promise<string | null> | null = null

function refreshAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

async function doRefresh(): Promise<string | null> {
  const token = tokenStore.getRefresh()
  if (!token) return null

  // A bare fetch, not request(): request() would recurse on its own 401. No
  // signal is passed either -- StockSearch aborts on every keystroke, and that
  // must not cancel a refresh other requests are waiting on.
  let res: Response
  try {
    res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
  } catch {
    // Network blip rather than a rejected token: keep what we have so the next
    // request can try again instead of signing the user out.
    return null
  }

  if (!res.ok) {
    tokenStore.clear() // notifies AuthContext, which drops to anonymous
    return null
  }

  const data = (await res.json()) as TokenResponse
  // Written before this promise resolves, so every awaiter reads the new token.
  tokenStore.set(data.access_token, data.refresh_token)
  return data.access_token
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let res = await send(path, options)

  // Retried at most once -- a server answering 401 forever cannot spin here.
  if (res.status === 401 && options.auth && tokenStore.getRefresh()) {
    const token = await refreshAccessToken()
    if (token) res = await send(path, options)
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* response was not JSON -- keep the status text */
    }
    throw new ApiError(detail, res.status)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** 台股大盤（發行量加權股價指數）. The API exposes it as an ordinary sid, so
 *  history / analysis / realtime all take the same routes a stock does. */
export const MARKET_INDEX_SID = 't00'

export const api = {
  // --- market data: public, no token is ever attached ---

  health: () => request<HealthResponse>('/api/health'),

  searchStocks: (q: string, limit = 20, signal?: AbortSignal) =>
    request<SearchResponse>(
      `/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      { signal },
    ),

  getStock: (sid: string) => request<StockInfo>(`/api/stocks/${sid}`),

  getHistory: (sid: string, months: number) =>
    request<HistoryResponse>(`/api/stocks/${sid}/history?months=${months}`),

  getDividends: (sid: string, years = 5) =>
    request<DividendResponse>(`/api/stocks/${sid}/dividends?years=${years}`),

  /** Rule-based technical analysis. An AI counterpart will sit next to this. */
  getTraditionalAnalysis: (sid: string, months: number, ruleSet: RuleSet = 'grs') =>
    request<TraditionalAnalysisResponse>(
      `/api/stocks/${sid}/analysis/traditional?months=${months}&rule_set=${ruleSet}`,
    ),

  getRealtime: (sids: string[]) =>
    request<RealtimeResponse>(`/api/realtime?sids=${sids.join(',')}`),

  // --- accounts ---

  register: (body: {
    username: string
    email: string
    password: string
    phone?: string
  }) => request<TokenResponse>('/api/auth/register', { method: 'POST', body }),

  login: (identifier: string, password: string) =>
    request<TokenResponse>('/api/auth/login', {
      method: 'POST',
      body: { identifier, password },
    }),

  logout: (refreshToken: string) =>
    request<void>('/api/auth/logout', {
      method: 'POST',
      body: { refresh_token: refreshToken },
    }),

  me: () => request<User>('/api/auth/me', { auth: true }),

  updateMe: (body: { email?: string; phone?: string }) =>
    request<User>('/api/auth/me', { method: 'PATCH', body, auth: true }),

  /** Set a new password. Returns a fresh token pair -- changing a password
   *  revokes every session the account has, including the caller's, so the
   *  response has to re-establish this one or the user is signed out as soon
   *  as the current access token expires. Store both tokens. */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<TokenResponse>('/api/auth/me/password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
      auth: true,
    }),

  // --- user administration (ADMIN only) ---

  listUsers: (q = '') =>
    request<UserListResponse>(
      `/api/users${q ? `?q=${encodeURIComponent(q)}` : ''}`,
      { auth: true },
    ),

  updateUser: (userId: number, body: { role?: Role; is_active?: boolean }) =>
    request<User>(`/api/users/${userId}`, { method: 'PATCH', body, auth: true }),

  /** Generate a password for a user who cannot sign in.
   *
   *  The plaintext comes back once and is never retrievable again -- the
   *  server keeps only the bcrypt hash. Whatever renders this must show it
   *  until the admin dismisses it, and must not stash it anywhere.
   */
  resetUserPassword: (userId: number) =>
    request<PasswordResetResponse>(`/api/users/${userId}/password-reset`, {
      method: 'POST',
      auth: true,
    }),

  deleteUser: (userId: number) =>
    request<void>(`/api/users/${userId}`, { method: 'DELETE', auth: true }),

  // --- background jobs (ADMIN only) ---

  /** Every job at once: schedule, last run, next run. Backs /admin/jobs. */
  listJobs: () => request<JobListResponse>('/api/jobs', { auth: true }),

  /** One job's audit trail, newest first, with the job itself for context. */
  listJobRuns: (jobId: string, limit = 50) =>
    request<JobRunsResponse>(`/api/jobs/${jobId}/runs?limit=${limit}`, {
      auth: true,
    }),

  /** Change when a job fires. Unset fields keep their current value; the
   *  server refuses anything outside that job's own min/max. */
  updateJobSchedule: (jobId: string, body: JobScheduleUpdate) =>
    request<Job>(`/api/jobs/${jobId}/schedule`, {
      method: 'PATCH',
      body,
      auth: true,
    }),

  /** Run a job now. Answers 202 as soon as it has *started* -- the work
   *  happens server-side and the caller polls the run log, because the
   *  listing sync alone takes ~40 s and this page lists several jobs.
   *  409 while that job is already running, 429 inside its cooldown. */
  runJob: (jobId: string) =>
    request<JobTriggerResponse>(`/api/jobs/${jobId}/run`, {
      method: 'POST',
      auth: true,
    }),

  // --- watchlist (signed in) ---

  getWatchlist: () => request<WatchlistResponse>('/api/watchlist', { auth: true }),

  putWatchlist: (sids: string[]) =>
    request<WatchlistResponse>('/api/watchlist', {
      method: 'PUT',
      body: { sids },
      auth: true,
    }),
}
