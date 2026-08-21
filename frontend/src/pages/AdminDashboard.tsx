import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { useI18n } from '../i18n'
import { fmtInt } from '../utils/format'
import { isStale, sinceLabel } from '../utils/jobs'

/**
 * The single entrance to everything an admin can do.
 *
 * The three admin areas used to be three buttons in the topbar, which does not
 * scale and gave no answer to "is anything wrong?" without visiting each one.
 * The tiles carry the one number that would send you into that page, so this
 * screen is worth loading even when nothing needs doing -- and the system
 * status that used to sit in the topbar as a permanent "DB connected" badge
 * lives here, where it is read on purpose instead of ignored all day.
 */
export default function AdminDashboard() {
  const { t } = useI18n()

  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  // Only the totals are rendered; the unfiltered listing is what /admin/users
  // already caches under the same key, so this is usually free. The key has to
  // match that page's shape exactly -- ['users', q, pendingOnly] -- or the two
  // views each fetch their own copy.
  const users = useQuery({
    queryKey: ['users', '', false],
    queryFn: () => api.listUsers(''),
  })
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: api.listJobs })

  const jobList = jobs.data?.jobs ?? []
  const failing = jobList.filter(
    (job) => isStale(job) || job.last_run?.status === 'failed',
  ).length

  const dbOk = health.data?.status === 'ok'
  const syncedAt = health.data?.stock_codes_synced_at ?? null

  return (
    <div className="stack">
      <div className="stack" style={{ gap: 4 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('admin.title')}
        </h2>
        <p className="dim" style={{ margin: 0 }}>
          {t('admin.lede')}
        </p>
      </div>

      <div className="admin-grid">
        <Link to="/admin/users" className="card admin-tile">
          <div className="card-title">{t('admin.usersTitle')}</div>
          <div className="admin-tile-stat tabular">
            {users.data ? t('admin.usersStat', { count: fmtInt(users.data.total) }) : '—'}
          </div>
          {/* Accounts waiting for approval are the only thing on this tile
              that somebody is actively blocked on, so they replace the
              description and take the same colour the jobs tile uses for
              "needs attention". */}
          <p
            className={`admin-tile-desc ${
              (users.data?.pending_total ?? 0) > 0 ? 'up' : 'dim'
            }`}
          >
            {(users.data?.pending_total ?? 0) > 0
              ? t('admin.usersPending', { count: users.data?.pending_total ?? 0 })
              : t('admin.usersDesc')}
          </p>
        </Link>

        <Link to="/admin/jobs" className="card admin-tile">
          <div className="card-title">{t('admin.jobsTitle')}</div>
          <div className="admin-tile-stat tabular">
            {jobs.data ? t('admin.jobsStat', { count: jobList.length }) : '—'}
          </div>
          <p className={`admin-tile-desc ${failing > 0 ? 'up' : 'dim'}`}>
            {jobs.data
              ? failing > 0
                ? t('admin.jobsAttention', { count: failing })
                : t('admin.jobsHealthy')
              : t('admin.jobsDesc')}
          </p>
        </Link>

        <Link to="/admin/stock-codes" className="card admin-tile">
          <div className="card-title">{t('admin.codesTitle')}</div>
          <div className="admin-tile-stat tabular">
            {health.data
              ? t('admin.listingCount', { count: fmtInt(health.data.stock_codes_loaded) })
              : '—'}
          </div>
          <p className="dim admin-tile-desc">
            {syncedAt
              ? t('admin.listingSynced', { when: sinceLabel(syncedAt, t) })
              : t('admin.listingNeverSynced')}
          </p>
        </Link>
      </div>

      <section className="card stack" style={{ gap: 10 }}>
        <div className="card-title" style={{ margin: 0 }}>
          {t('admin.system')}
        </div>
        <div className="row-between wrap">
          <span className="dim">{t('admin.database')}</span>
          <span className="row" title={health.data?.database}>
            <span className={`dot ${dbOk ? 'dot-ok' : 'dot-bad'}`} />
            {health.data
              ? dbOk
                ? t('health.connected')
                : t('health.disconnected')
              : '—'}
          </span>
        </div>
        <div className="row-between wrap">
          <span className="dim">{t('admin.scheduler')}</span>
          <span>
            {jobs.data
              ? jobs.data.scheduler_enabled
                ? t('admin.schedulerOn', { timezone: jobs.data.timezone })
                : t('admin.schedulerOff')
              : '—'}
          </span>
        </div>
      </section>
    </div>
  )
}
