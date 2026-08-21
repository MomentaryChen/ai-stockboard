#!/usr/bin/env node
/**
 * Capture the screenshots README.md embeds, from a stack that is already up.
 *
 * This lives in tools/ rather than in frontend/ because frontend/Dockerfile
 * runs `pnpm install --frozen-lockfile` with devDependencies included -- a
 * Playwright dependency there would pull a browser download into every image
 * build, for a tool the image never runs.
 *
 * The script only reads: it signs in with credentials the caller supplies and
 * navigates. Views that need a session are skipped, loudly, when
 * SHOT_EMAIL/SHOT_PASSWORD are absent, so a contributor without an account
 * still refreshes the public screenshots instead of getting an error.
 *
 * Usage:
 *   pnpm install && pnpm setup      # once: fetch the Chromium build
 *   pnpm capture
 *
 * Environment:
 *   SHOT_BASE_URL   default http://localhost:8100 (docker compose frontend)
 *   SHOT_API_URL    default = SHOT_BASE_URL; nginx and the Vite dev server
 *                   both proxy /api, so only a split deployment needs this
 *   SHOT_OUT_DIR    default <repo>/docs/screenshots
 *   SHOT_EMAIL      account used for the signed-in and admin views
 *   SHOT_PASSWORD   its password
 *   SHOT_LOCALE     zh-TW (default) or en
 *   SHOT_WIDTH      viewport width, default 1440
 *   SHOT_HEIGHT     viewport height, default 900
 *   SHOT_SCALE      device pixel ratio, default 2 (retina-sharp in the README)
 *   SHOT_SETTLE_MS  pause after the network goes quiet, default 1200. Recharts
 *                   animates its series in JS, so "no pending request" is not
 *                   the same as "the chart has finished drawing".
 *   SHOT_ONLY       comma-separated shot names, for re-taking just one
 */

import { mkdir } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')

const stripSlash = (url) => url.replace(/\/+$/, '')

const BASE_URL = stripSlash(process.env.SHOT_BASE_URL ?? 'http://localhost:8100')
const API_URL = stripSlash(process.env.SHOT_API_URL ?? BASE_URL)
const OUT_DIR = resolve(process.env.SHOT_OUT_DIR ?? join(REPO_ROOT, 'docs', 'screenshots'))
const LOCALE = process.env.SHOT_LOCALE ?? 'zh-TW'
const WIDTH = Number(process.env.SHOT_WIDTH ?? 1440)
const HEIGHT = Number(process.env.SHOT_HEIGHT ?? 900)
const SCALE = Number(process.env.SHOT_SCALE ?? 2)
const SETTLE_MS = Number(process.env.SHOT_SETTLE_MS ?? 1200)
const EMAIL = process.env.SHOT_EMAIL
const PASSWORD = process.env.SHOT_PASSWORD
const ONLY = (process.env.SHOT_ONLY ?? '')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean)

// Mirrors frontend/src/api/tokenStore.ts and frontend/src/i18n/storage.ts.
// Seeding these beats driving the login form: one fewer moving part between a
// failed capture and its cause, and the language is pinned rather than left to
// whatever Accept-Language the headless browser sends.
const ACCESS_KEY = 'ai-stockboard.access_token'
const REFRESH_KEY = 'ai-stockboard.refresh_token'
const LOCALE_KEY = 'ai-stockboard.locale'

/**
 * `auth` decides which browser context takes the shot:
 *
 *   'none'      always signed out -- the view *is* the signed-out state
 *   'optional'  signed in when credentials were given, signed out otherwise.
 *               The market and stock boards are public, but half of what they
 *               show (live opening prices, the watchlist) reads "login
 *               required" without a session, which makes a poor screenshot.
 *   'user'      needs any session
 *   'admin'     needs role ADMIN
 *
 * `fullPage` is for views whose point is the whole stack of cards. The rest
 * are framed to the viewport, which also avoids the dead space a full-page
 * shot leaves when one column runs much longer than the other.
 */
const SHOTS = [
  {
    name: 'market-dashboard',
    path: '/',
    auth: 'optional',
    fullPage: true,
  },
  {
    name: 'stock-detail',
    path: '/stock/2330',
    auth: 'optional',
  },
  {
    name: 'login',
    path: '/login',
    auth: 'none',
  },
  {
    name: 'realtime-board',
    path: '/realtime',
    auth: 'user',
    fullPage: true,
  },
  {
    name: 'admin-dashboard',
    path: '/admin',
    auth: 'admin',
  },
  {
    name: 'admin-jobs',
    path: '/admin/jobs',
    auth: 'admin',
    fullPage: true,
  },
  {
    name: 'admin-users',
    path: '/admin/users',
    auth: 'admin',
    fullPage: true,
    // The account table is real data from whichever database the run points
    // at, and this image gets committed. Username, email and phone are the
    // first three columns; the roles, states and controls that the screenshot
    // exists to show are all to the right of them.
    mask: ['.card tbody td:nth-child(1)', '.card tbody td:nth-child(2)', '.card tbody td:nth-child(3)'],
  },
  {
    name: 'admin-stock-codes',
    path: '/admin/stock-codes',
    auth: 'admin',
    fullPage: true,
  },
]

const results = { saved: [], skipped: [], failed: [] }

function log(level, message) {
  const prefix = { ok: '  ok  ', skip: ' skip ', fail: ' fail ', info: '      ' }[level]
  console.log(`${prefix}${message}`)
}

async function postJson(path, body) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`POST ${path} -> ${res.status} ${detail.slice(0, 200)}`)
  }
  return res.json()
}

/**
 * Trade the credentials for a token pair, or return null when none were given.
 * A bad password is fatal rather than a downgrade to anonymous: the caller
 * clearly meant to capture the signed-in views, and quietly shipping only the
 * public ones would leave a stale admin screenshot in the README.
 */
async function signIn() {
  if (!EMAIL || !PASSWORD) return null
  const tokens = await postJson('/api/auth/login', { identifier: EMAIL, password: PASSWORD })
  const user = tokens.user ?? {}
  if (user.must_change_password) {
    throw new Error(
      `${EMAIL} is holding an admin-generated password. The app pins such an ` +
        'account to /change-password, so no other view can be captured with it.',
    )
  }
  return { ...tokens, isAdmin: user.role === 'ADMIN' }
}

async function waitUntilSettled(page) {
  // networkidle covers the react-query fetches; the spinner check covers the
  // gap between "the response arrived" and "the card re-rendered with it".
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await page
    .waitForFunction(() => document.querySelectorAll('.spinner').length === 0, null, {
      timeout: 15000,
    })
    .catch(() => {})
  if (SETTLE_MS > 0) await page.waitForTimeout(SETTLE_MS)
}

/**
 * Height of the rendered UI, when it stops short of the viewport.
 *
 * `.app` is `min-height: 100%`, so its box always reports the full viewport
 * and cannot answer this; `.main` is a flex child sized by its content and
 * can. Views with only a few cards -- the realtime board, the admin hub --
 * otherwise ship a third of an image of empty background.
 */
async function contentClip(page, fullPage) {
  const measured = await page
    .evaluate(() => {
      const main = document.querySelector('.main')
      if (!main) return null
      const box = main.getBoundingClientRect()
      return {
        // Page coordinates: the capture never scrolls, but be explicit anyway.
        bottom: Math.ceil(box.bottom + window.scrollY),
        scrollHeight: document.documentElement.scrollHeight,
      }
    })
    .catch(() => null)

  if (!measured?.bottom) return undefined
  const captured = fullPage ? measured.scrollHeight : HEIGHT
  // A margin, so a shot that merely rounds a pixel short keeps its full frame.
  if (measured.bottom >= captured - 24) return undefined
  return { x: 0, y: 0, width: WIDTH, height: measured.bottom }
}

async function capture(context, shot) {
  const { path } = shot
  const page = await context.newPage()
  try {
    await page.goto(`${BASE_URL}${path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await waitUntilSettled(page)

    // A route guard that bounced us elsewhere means the shot would show the
    // wrong screen -- report it instead of saving a misleading image.
    const landed = new URL(page.url()).pathname
    if (landed !== path) {
      results.skipped.push([shot.name, `redirected to ${landed}`])
      log('skip', `${shot.name} -- redirected to ${landed}`)
      return
    }

    const file = join(OUT_DIR, `${shot.name}.png`)
    const fullPage = Boolean(shot.fullPage)
    await page.screenshot({
      path: file,
      fullPage,
      clip: await contentClip(page, fullPage),
      mask: (shot.mask ?? []).map((selector) => page.locator(selector)),
      // Playwright paints masks magenta by default, which reads as a rendering
      // fault in a dark UI. A flat slate reads as "redacted".
      maskColor: '#39414f',
    })
    results.saved.push(shot.name)
    log('ok', `${shot.name}.png  <- ${path}`)
  } catch (error) {
    results.failed.push([shot.name, error.message])
    log('fail', `${shot.name} -- ${error.message}`)
  } finally {
    await page.close()
  }
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true })

  const session = await signIn()
  if (!session) {
    log('info', 'SHOT_EMAIL/SHOT_PASSWORD unset -- capturing the public views only.')
  } else if (!session.isAdmin) {
    log('info', `signed in as ${EMAIL} (not ADMIN) -- admin views will be skipped.`)
  } else {
    log('info', `signed in as ${EMAIL} (ADMIN).`)
  }

  const browser = await chromium.launch()

  /**
   * Two contexts rather than one, because the token is seeded into
   * localStorage before the first navigation and cannot be dropped per page:
   * a single signed-in context would bounce /login to the board and lose that
   * screenshot, while a single signed-out one would show "login required"
   * where the market board should show prices.
   */
  async function makeContext(tokens) {
    const context = await browser.newContext({
      viewport: { width: WIDTH, height: HEIGHT },
      deviceScaleFactor: SCALE,
      locale: LOCALE,
      // Trims the mount animation for anything that honours the media query.
      reducedMotion: 'reduce',
    })
    await context.addInitScript(
      (values) => {
        try {
          localStorage.setItem(values.localeKey, values.locale)
          if (values.access && values.refresh) {
            localStorage.setItem(values.accessKey, values.access)
            localStorage.setItem(values.refreshKey, values.refresh)
          }
        } catch {
          /* storage unavailable -- the run will simply look signed out */
        }
      },
      {
        accessKey: ACCESS_KEY,
        refreshKey: REFRESH_KEY,
        localeKey: LOCALE_KEY,
        locale: LOCALE,
        access: tokens?.access_token ?? null,
        refresh: tokens?.refresh_token ?? null,
      },
    )
    return context
  }

  const anonContext = await makeContext(null)
  const authContext = session ? await makeContext(session) : null

  try {
    for (const shot of SHOTS) {
      if (ONLY.length && !ONLY.includes(shot.name)) continue

      if ((shot.auth === 'user' || shot.auth === 'admin') && !session) {
        results.skipped.push([shot.name, 'needs a session; SHOT_EMAIL/SHOT_PASSWORD unset'])
        log('skip', `${shot.name} -- needs a session (set SHOT_EMAIL/SHOT_PASSWORD)`)
        continue
      }
      if (shot.auth === 'admin' && !session.isAdmin) {
        results.skipped.push([shot.name, 'needs an ADMIN account'])
        log('skip', `${shot.name} -- needs an ADMIN account`)
        continue
      }

      const context = shot.auth === 'none' ? anonContext : (authContext ?? anonContext)
      await capture(context, shot)
    }
  } finally {
    await anonContext.close()
    if (authContext) await authContext.close()
    await browser.close()
  }

  console.log(
    `\n${results.saved.length} saved, ${results.skipped.length} skipped, ` +
      `${results.failed.length} failed  ->  ${OUT_DIR}`,
  )
  // A skip is a deliberate outcome (no credentials); a failure is not.
  if (results.failed.length) process.exitCode = 1
}

main().catch((error) => {
  console.error(`\ncapture aborted: ${error.message}`)
  process.exitCode = 1
})
