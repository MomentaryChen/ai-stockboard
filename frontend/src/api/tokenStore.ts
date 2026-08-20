/**
 * Where the JWT pair lives.
 *
 * A plain module rather than React state, because `client.ts` needs the token
 * and is not a component. React subscribes through `useSyncExternalStore` in
 * AuthContext, so the two stay in step without the API layer importing React.
 *
 * Known trade-off: localStorage is readable by any XSS on the page. The
 * alternative -- httpOnly cookies -- would need CSRF tokens and SameSite rules
 * that work across three origins (Vite's :5173, nginx's :8100, the server's
 * :8000), while nginx and the Vite proxy already forward `Authorization`
 * untouched. Short access-token lifetimes bound the exposure.
 */

const ACCESS_KEY = 'ai-stockboard.access_token'
const REFRESH_KEY = 'ai-stockboard.refresh_token'

// Safari private mode throws on read *and* write, so both are guarded.
function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* storage unavailable -- the session just won't survive a reload */
  }
}

let access = read(ACCESS_KEY)
let refresh = read(REFRESH_KEY)

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

export const tokenStore = {
  getAccess: () => access,
  getRefresh: () => refresh,

  subscribe(listener: () => void) {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  set(newAccess: string, newRefresh: string) {
    access = newAccess
    refresh = newRefresh
    write(ACCESS_KEY, newAccess)
    write(REFRESH_KEY, newRefresh)
    emit()
  },

  clear() {
    access = null
    refresh = null
    write(ACCESS_KEY, null)
    write(REFRESH_KEY, null)
    emit()
  },
}

// Signing out in one tab signs the others out too.
window.addEventListener('storage', (event) => {
  if (event.key === ACCESS_KEY || event.key === REFRESH_KEY) {
    access = read(ACCESS_KEY)
    refresh = read(REFRESH_KEY)
    emit()
  }
})
