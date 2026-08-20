import { Link, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from './api/client'
import { useAuth } from './auth/AuthContext'
import RequireAuth from './components/RequireAuth'
import UserMenu from './components/UserMenu'
import AdminStockCodes from './pages/AdminStockCodes'
import AdminUsers from './pages/AdminUsers'
import Login from './pages/Login'
import MarketDashboard from './pages/MarketDashboard'
import Register from './pages/Register'
import StockDetail from './pages/StockDetail'
import RealtimeBoard from './pages/RealtimeBoard'

function HealthIndicator() {
  const { data } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 60_000,
  })

  if (!data) return null

  const ok = data.status === 'ok'
  return (
    <span className="row dim" title={`PostgreSQL: ${data.database}`}>
      <span className={`dot ${ok ? 'dot-ok' : 'dot-bad'}`} />
      {ok ? 'DB 已連線' : 'DB 未連線'}
    </span>
  )
}

export default function App() {
  const { pathname } = useLocation()
  const { status } = useAuth()

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span>ai</span>-stockboard 台股看板
        </div>
        <nav className="nav">
          <Link to="/" className={pathname === '/' ? 'active' : ''}>
            大盤
          </Link>
          {/* Any /stock/:sid keeps the tab lit, not just the default 2330. */}
          <Link to="/stock/2330" className={pathname.startsWith('/stock') ? 'active' : ''}>
            個股分析
          </Link>
          <Link
            to="/realtime"
            className={pathname.startsWith('/realtime') ? 'active' : ''}
            title={status === 'anonymous' ? '即時報價需要登入' : undefined}
          >
            即時報價
            {/* Says so before the click rather than after it. Only once the
                session is known to be absent -- 'loading' would flash it. */}
            {status === 'anonymous' && <span className="nav-lock">需登入</span>}
          </Link>
        </nav>
        <div className="topbar-right">
          <HealthIndicator />
          <UserMenu />
        </div>
      </header>

      <main className="main">
        <Routes>
          {/* 台股大盤 is the landing view; individual stocks hang off it. */}
          <Route path="/" element={<MarketDashboard />} />
          <Route path="/stock/:sid" element={<StockDetail />} />
          <Route path="/realtime" element={<RealtimeBoard />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route
            path="/admin/users"
            element={
              <RequireAuth adminOnly>
                <AdminUsers />
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
          <Route path="*" element={<div className="center-note">找不到這個頁面</div>} />
        </Routes>
      </main>
    </div>
  )
}
