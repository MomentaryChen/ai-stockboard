import { Link, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import PasswordGate from './components/PasswordGate'
import RequireAuth from './components/RequireAuth'
import UserMenu from './components/UserMenu'
import { useI18n } from './i18n'
import { useMiniView } from './utils/view'
import AdminDashboard from './pages/AdminDashboard'
import AdminJobDetail from './pages/AdminJobDetail'
import AdminJobs from './pages/AdminJobs'
import AdminStockCodes from './pages/AdminStockCodes'
import AdminUsers from './pages/AdminUsers'
import ChangePassword from './pages/ChangePassword'
import Login from './pages/Login'
import MarketDashboard from './pages/MarketDashboard'
import Register from './pages/Register'
import StockDetail from './pages/StockDetail'
import RealtimeBoard from './pages/RealtimeBoard'

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
          <span>ai</span>
          {t('app.brandSuffix')}
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
        {/* Outside <Routes> so it covers the public market views too: an
            account holding an ADMIN-generated password has nothing it may do
            until it picks its own. */}
        <PasswordGate>
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
            <Route path="*" element={<div className="center-note">{t('nav.notFound')}</div>} />
          </Routes>
        </PasswordGate>
      </main>
    </div>
  )
}
