import { tokenStore } from './tokenStore'
import type {
  AiAnalysisResponse,
  AiQuotaStatus,
  BacktestBatchResponse,
  BacktestResponse,
  Job,
  JobListResponse,
  JobRunsResponse,
  JobScheduleUpdate,
  JobTriggerResponse,
  PasswordResetResponse,
  RegisterResponse,
  RegistrationPolicy,
  Role,
  RuleSet,
  TokenResponse,
  TraditionalAnalysisBatchResponse,
  TraditionalAnalysisResponse,
  HealthResponse,
  HistoryResponse,
  MarketOpenResponse,
  DividendResponse,
  ChipResponse,
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
  /** Seconds from the `Retry-After` header, when the server sent one.
   *
   *  Only the 429s carry it, and it is the difference between telling a
   *  locked-out user "try again later" and "try again in 12 minutes" -- the
   *  second answer is the one that stops them retrying for the whole window.
   */
  readonly retryAfter: number | null

  constructor(message: string, status: number, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
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

/** The 403 detail for an account that has registered but not been approved.
 *
 *  Matches deps.ACCOUNT_PENDING_APPROVAL on the server. Narrowed on rather
 *  than displayed, because the server's copy is English and this one has a
 *  translation -- and because "awaiting approval" needs to read as progress,
 *  not as the rejection that the same 403 status otherwise implies.
 */
export const ACCOUNT_PENDING_APPROVAL = 'Account is awaiting approval'

/** The two 429 details, both of which arrive with a Retry-After.
 *  Match deps.ACCOUNT_LOCKED / deps.TOO_MANY_ATTEMPTS. */
export const ACCOUNT_LOCKED = 'Account temporarily locked'
export const TOO_MANY_ATTEMPTS = 'Too many sign-in attempts'

export function isPendingApproval(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    error.message === ACCOUNT_PENDING_APPROVAL
  )
}

/** What a request that ran out of time reports as. 408 is the client's own
 *  verdict, not the server's -- nothing upstream answered at all. The string is
 *  a fallback: `errorMessage()` swaps it for translated copy before a user sees
 *  it, because unlike a server `detail` this sentence is ours to write. */
const REQUEST_TIMEOUT = 'Request timed out'

/** True for a request this client gave up on rather than one the server refused. */
export function isTimeout(error: unknown): boolean {
  return error instanceof ApiError && error.status === 408
}

/** How long a request may run before the client stops waiting.
 *
 *  There was no limit at all, and nginx only cuts the proxy at 180 s: a
 *  connection that stalled left the spinner turning for three minutes and then
 *  failed anyway. Most routes answer out of Postgres in well under a second,
 *  so 15 s is already an outlier for them.
 */
const DEFAULT_TIMEOUT_MS = 15_000

/** The budget for routes that fall through to TWSE on a cold cache.
 *
 *  A first-ever history fetch really does run ~18 s, so the default would
 *  abort work that was going to succeed. 60 s leaves that case three times the
 *  room it needs while still giving up long before nginx does -- the point is
 *  to have *a* ceiling, not a tight one.
 */
const SLOW_TIMEOUT_MS = 60_000

/** One request's abort budget: the caller's signal, plus a deadline.
 *
 *  `AbortSignal.any()` would express this in a line, but it cannot say *which*
 *  of the two fired, and that distinction is the whole point -- a caller
 *  cancelling (StockSearch aborts on every keystroke) must stay an AbortError
 *  that react-query ignores, while a deadline must surface as an error the
 *  user sees.
 */
interface Budget {
  signal: AbortSignal
  expired: () => boolean
  dispose: () => void
}

function budgetFor(caller: AbortSignal | undefined, timeoutMs: number): Budget {
  const controller = new AbortController()
  let expired = false

  const timer = setTimeout(() => {
    expired = true
    controller.abort()
  }, timeoutMs)

  const relay = () => controller.abort()
  if (caller) {
    if (caller.aborted) controller.abort()
    else caller.addEventListener('abort', relay)
  }

  return {
    signal: controller.signal,
    expired: () => expired,
    dispose: () => {
      clearTimeout(timer)
      caller?.removeEventListener('abort', relay)
    },
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  /** Send the access token, and retry once after a refresh on 401. */
  auth?: boolean
  /** Override the deadline. Only the routes that can legitimately outlast
   *  DEFAULT_TIMEOUT_MS pass this; see SLOW_TIMEOUT_MS. */
  timeoutMs?: number
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const {
    method = 'GET',
    body,
    signal,
    auth = false,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options
  const headers: Record<string, string> = {}

  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = tokenStore.getAccess()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  // Per attempt, not per call: the retry after a token refresh is a second
  // request over a connection the first one proved nothing about, so it gets
  // its own budget rather than the remainder of one.
  const budget = budgetFor(signal, timeoutMs)
  try {
    return await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: budget.signal,
    })
  } catch (error) {
    if (budget.expired()) throw new ApiError(REQUEST_TIMEOUT, 408)
    throw error
  } finally {
    budget.dispose()
  }
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
  // caller signal is passed either -- StockSearch aborts on every keystroke,
  // and that must not cancel a refresh other requests are waiting on. It still
  // gets a deadline: every authenticated request that 401'd is awaiting this
  // one promise, so a refresh that hangs hangs all of them.
  const budget = budgetFor(undefined, DEFAULT_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
      signal: budget.signal,
    })
  } catch {
    // Network blip rather than a rejected token: keep what we have so the next
    // request can try again instead of signing the user out. A timeout lands
    // here too, and means the same thing.
    return null
  } finally {
    budget.dispose()
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

/** FastAPI's `detail`, as one line of text.
 *
 *  It is a string for everything the app raises itself (HTTPException), but a
 *  *list of objects* for the 422 that pydantic produces when a body fails
 *  validation. That list used to be assigned to `detail` untouched and then
 *  interpolated into a message template, where the user read `[object Object]`
 *  instead of being told which field was wrong -- on register and change
 *  password, the two forms most likely to produce a 422 in the first place.
 *
 *  Returns null rather than a placeholder when nothing usable is there, so the
 *  caller keeps its own `HTTP <status>` fallback.
 */
function readDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail || null
  if (Array.isArray(detail)) {
    const lines = detail.map(readValidationError).filter(Boolean)
    return lines.length ? lines.join('; ') : null
  }
  return null
}

/** One pydantic error: `{ loc: ['body', 'password'], msg: '...' }`.
 *
 *  `loc[0]` is the part of the request that failed ("body", "query", "path"),
 *  which tells the reader nothing they can act on -- the rest is the field
 *  path, and that is what the form labels. An entry shaped differently
 *  degrades to whichever half is present rather than to "[object Object]".
 */
function readValidationError(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (!entry || typeof entry !== 'object') return ''

  const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
  const message = typeof msg === 'string' ? msg : ''
  const field = Array.isArray(loc)
    ? loc
        .slice(1)
        .filter(
          (part): part is string | number =>
            typeof part === 'string' || typeof part === 'number',
        )
        .join('.')
    : ''

  if (field && message) return `${field}: ${message}`
  return message || field
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
      detail = readDetail(body?.detail) ?? detail
    } catch {
      /* response was not JSON -- keep the status text */
    }
    // Only ever the delta-seconds form here; the HTTP-date form the RFC also
    // allows is never sent by this API, so Number() is enough.
    const retryAfter = Number(res.headers.get('Retry-After'))
    throw new ApiError(
      detail,
      res.status,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
    )
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** 台股大盤（發行量加權股價指數）. The API exposes it as an ordinary sid, so
 *  history / analysis / realtime all take the same routes a stock does. */
export const MARKET_INDEX_SID = 't00'

export const api = {
  // --- market data: public apart from getRealtime, which is signed-in only ---

  health: () => request<HealthResponse>('/api/health'),

  searchStocks: (q: string, limit = 20, signal?: AbortSignal) =>
    request<SearchResponse>(
      `/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      { signal },
    ),

  getStock: (sid: string) => request<StockInfo>(`/api/stocks/${sid}`),

  getHistory: (sid: string, months: number) =>
    request<HistoryResponse>(`/api/stocks/${sid}/history?months=${months}`, {
      timeoutMs: SLOW_TIMEOUT_MS,
    }),

  /** 當日開盤情報 for the index on one trading day.
   *
   *  Public and cache-only, so it works signed out. `date` is a trading day in
   *  Taipei terms -- omit it and the server answers for today on the exchange's
   *  calendar, which is not necessarily the browser's.
   *
   *  The endpoint also takes a `sids` list to fold extra codes into the same
   *  answer; nothing asks for that since the market board stopped carrying the
   *  watchlist, so this wrapper does not offer it.
   */
  getMarketOpen: (date: string) =>
    request<MarketOpenResponse>(`/api/market/open?date=${date}`, {
      timeoutMs: SLOW_TIMEOUT_MS,
    }),

  getDividends: (sid: string, years = 5) =>
    request<DividendResponse>(`/api/stocks/${sid}/dividends?years=${years}`, {
      timeoutMs: SLOW_TIMEOUT_MS,
    }),

  /** Institutional net buying and margin balances. Cache-first; a cold
   *  date range still hits TWSE, so this uses the slow timeout. */
  getChips: (sid: string, days = 10) =>
    request<ChipResponse>(`/api/stocks/${sid}/chips?days=${days}`, {
      timeoutMs: SLOW_TIMEOUT_MS,
    }),

  /** Rule-based technical analysis. An AI counterpart will sit next to this. */
  getTraditionalAnalysis: (sid: string, months: number, ruleSet: RuleSet = 'grs') =>
    request<TraditionalAnalysisResponse>(
      `/api/stocks/${sid}/analysis/traditional?months=${months}&rule_set=${ruleSet}`,
      { timeoutMs: SLOW_TIMEOUT_MS },
    ),

  /** Watchlist-sized BFP: Buy / Sell / Don't touch, no MA series. */
  getTraditionalAnalysisBatch: (sids: string[], ruleSet: RuleSet = 'grs') =>
    request<TraditionalAnalysisBatchResponse>(
      `/api/analysis/traditional?sids=${sids.join(',')}&rule_set=${ruleSet}`,
      { timeoutMs: SLOW_TIMEOUT_MS },
    ),

  /** How the four-point verdict has actually performed over the past year.
   *
   *  No `months`: the window is fixed server-side, because a short one answers
   *  with win rates drawn from two or three signals. Cache-only upstream, so
   *  this is cheap and works signed out -- but a stock whose history has never
   *  been loaded answers 422, which the card renders as "not enough bars yet"
   *  rather than as an error. */
  getBacktest: (sid: string, ruleSet: RuleSet = 'grs') =>
    request<BacktestResponse>(
      `/api/stocks/${sid}/analysis/backtest?rule_set=${ruleSet}`,
    ),

  /** Several stocks plus the pooled rates across them. `pooled` is the figure
   *  worth reading: one stock over one year is a handful of samples. */
  getBacktestBatch: (sids: string[], ruleSet: RuleSet = 'grs') =>
    request<BacktestBatchResponse>(
      `/api/analysis/backtest?sids=${sids.join(',')}&rule_set=${ruleSet}`,
    ),

  /** Ask the AI engine for a position call: enter / exit / hold, and at what size.
   *
   *  POST, and signed in, because a miss costs a Gemini request. The server
   *  caches per (stock, trading day, model, prompt) and shares that cache across
   *  accounts, so pressing this twice on the same session is free the second
   *  time -- `cached` on the response says which of the two happened.
   *
   *  There is no `months` parameter on purpose: the history window is part of
   *  what the verdict was computed from, and letting callers vary it would make
   *  the cached answer depend on whoever asked first.
   */
  generateAiAnalysis: (sid: string, locale: string) =>
    request<AiAnalysisResponse>(
      `/api/stocks/${sid}/analysis/ai?locale=${encodeURIComponent(locale)}`,
      { method: 'POST', auth: true },
    ),

  /** Generations left today. Read up front so the button can explain itself
   *  before it is pressed, rather than answering 429 afterwards. */
  getAiQuota: () => request<AiQuotaStatus>('/api/analysis/ai/quota', { auth: true }),

  /** Signed in only -- the one market-data route that is not public. `auth`
   *  also buys the refresh-and-retry, which a 10-second poll needs. */
  getRealtime: (sids: string[]) =>
    request<RealtimeResponse>(`/api/realtime?sids=${sids.join(',')}`, { auth: true }),

  // --- accounts ---

  /** What signing up will do. Public, and read before the form renders --
   *  under review, submitting does not sign you in, and finding that out
   *  afterwards reads as a broken signup. */
  getRegistrationPolicy: () =>
    request<RegistrationPolicy>('/api/auth/registration-policy'),

  /** Create an account. Check `pending` before touching `tokens`: an account
   *  awaiting approval comes back without a session, on purpose. */
  register: (body: {
    username: string
    email: string
    password: string
    phone?: string
  }) => request<RegisterResponse>('/api/auth/register', { method: 'POST', body }),

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

  listUsers: (q = '', pendingOnly = false) => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (pendingOnly) params.set('pending', 'true')
    const query = params.toString()
    return request<UserListResponse>(`/api/users${query ? `?${query}` : ''}`, {
      auth: true,
    })
  },

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

  /** Lift a lockout without waiting out its timer, and without touching the
   *  user's password -- they may have been typing it correctly all along.
   *  Idempotent on an account that is not locked. */
  unlockUser: (userId: number) =>
    request<User>(`/api/users/${userId}/unlock`, { method: 'POST', auth: true }),

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
