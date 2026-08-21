import { Suspense, lazy } from 'react'
import { Link, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import ErrorBoundary from './components/ErrorBoundary'
import PasswordGate from './components/PasswordGate'
import RequireAuth from './components/RequireAuth'
import UserMenu from './components/UserMenu'
import { useI18n } from './i18n'
import { useMiniView } from './utils/view'
import MarketDashboard from './pages/MarketDashboard'

/*
 * Every route but the landing one is a chunk of its own.
 *
 * They were all static imports, so a first-time visitor arriving at 大盤
 * downloaded the five admin pages -- a console they may never be allowed to
 * open -- before anything rendered. 大盤 itself stays eager: it is what the
 * entry bundle exists to render, and making it lazy would only add a round
 * trip in front of the page everyone lands on.
 *
 * The chart stack (Recharts) is shared between 大盤 and 個股分析, so the
 * bundler keeps it in a common chunk rather than duplicating it per route.
 */
const StockDetail = lazy(() => import('./pages/StockDetail'))
const RealtimeBoard = lazy(() => import('./pages/RealtimeBoard'))
const Login = lazy(() => import('./pages/Login'))
const Register = lazy(() => import('./pages/Register'))
const ChangePassword = lazy(() => import('./pages/ChangePassword'))
const AdminDashboard = lazy(() => import('./pages/AdminDashboard'))
const AdminUsers = lazy(() => import('./pages/AdminUsers'))
const AdminJobs = lazy(() => import('./pages/AdminJobs'))
const AdminJobDetail = lazy(() => import('./pages/AdminJobDetail'))
const AdminStockCodes = lazy(() => import('./pages/AdminStockCodes'))

export default function App() {
  const { pathname } = useLocation()
  const { status } = useAuth()
  const { t } = useI18n()
  // `?view=mini` turns a page into a widget: no topbar, no page padding, no
  // max-width. It exists so /realtime can be opened in a 400px window and
  // parked beside real work, where every row of chrome costs a stock.
  const mini = useMiniView()

  return (
    <div className={`app${mini ? ' app-mini' : ''}`}>
      <header className="topbar" hidden={mini}>
        <div className="brand">
          <span className="brand-mark">ai</span>
          {/* Its own element so a narrow screen can drop the words and keep
              the mark; the topbar has no room for both. */}
          <span className="brand-rest">{t('app.brandSuffix')}</span>
        </div>
        <nav className="nav">
          <Link to="/" className={pathname === '/' ? 'active' : ''}>
            {t('nav.market')}
          </Link>
          {/* Any /stock/:sid keeps the tab lit, not just the default 2330. */}
          <Link to="/stock/2330" className={pathname.startsWith('/stock') ? 'active' : ''}>
            {t('nav.stock')}
          </Link>
          <Link
            to="/realtime"
            className={pathname.startsWith('/realtime') ? 'active' : ''}
            title={status === 'anonymous' ? t('nav.realtimeLockedTitle') : undefined}
          >
            {t('nav.realtime')}
            {/* Says so before the click rather than after it. Only once the
                session is known to be absent -- 'loading' would flash it. */}
            {status === 'anonymous' && (
              <span className="nav-lock">{t('nav.realtimeLocked')}</span>
            )}
          </Link>
        </nav>
        <div className="topbar-right">
          <UserMenu />
        </div>
      </header>

      <main className={`main${mini ? ' main-mini' : ''}`}>
        {/* Outside <PasswordGate> so the topbar survives whatever threw: a
            render error below this point used to unmount the whole app, and
            the only way back was a reload that also dropped the query cache
            and re-ran session restore. Keyed on the path, so navigating to a
            page that works clears the fallback. */}
        <ErrorBoundary resetKey={pathname}>
          {/* Outside <Routes> so it covers the public market views too: an
              account holding an ADMIN-generated password has nothing it may do
              until it picks its own. */}
          <PasswordGate>
            {/* Every lazy route resolves under here. The fallback is a bare
                spinner rather than a skeleton because these chunks are small
                and same-origin -- naming what is loading would flash. */}
            <Suspense
              fallback={
                <div className="center-note">
                  <span className="spinner" />
                </div>
              }
            >
              <Routes>
                {/* 台股大盤 is the landing view; individual stocks hang off it. */}
                <Route path="/" element={<MarketDashboard />} />
                <Route path="/stock/:sid" element={<StockDetail />} />
                <Route path="/realtime" element={<RealtimeBoard />} />
                <Route path="/login" element={<Login />} />
                <Route path="/register" element={<Register />} />
                <Route
                  path="/change-password"
                  element={
                    <RequireAuth>
                      <ChangePassword />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/admin"
                  element={
                    <RequireAuth adminOnly>
                      <AdminDashboard />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/admin/users"
                  element={
                    <RequireAuth adminOnly>
                      <AdminUsers />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/admin/jobs"
                  element={
                    <RequireAuth adminOnly>
                      <AdminJobs />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/admin/jobs/:jobId"
                  element={
                    <RequireAuth adminOnly>
                      <AdminJobDetail />
                    </RequireAuth>
                  }
                />
                <Route
                  path="/admin/stock-codes"
                  element={
                    <RequireAuth adminOnly>
                      <AdminStockCodes />
                    </RequireAuth>
                  }
                />
                <Route
                  path="*"
                  element={<div className="center-note">{t('nav.notFound')}</div>}
                />
              </Routes>
            </Suspense>
          </PasswordGate>
        </ErrorBoundary>
      </main>
    </div>
  )
}
