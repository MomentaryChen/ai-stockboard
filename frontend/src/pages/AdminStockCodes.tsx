import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import JobRunsTable from '../components/JobRunsTable'
import { useI18n } from '../i18n'
import { fmtInt } from '../utils/format'
import { isStale, runErrorMessage, scheduleLabel, sinceLabel, untilLabel } from '../utils/jobs'

/** The job this page is about. It is one of several in the registry; this view
 *  exists because the listing has consequences the generic console cannot
 *  explain -- a stale sync means newly listed stocks cannot be found at all,
 *  which is a support ticket, not a red dot. */
const JOB_ID = 'stock_code_sync'

/**
 * The listing sync, with the context that only applies to it.
 *
 * The schedule and the full history live in the generic console at
 * /admin/jobs/stock_code_sync; this page deliberately does not duplicate the
 * editor. What it adds is what the listing itself looks like right now, which
 * comes from /api/health rather than the job log -- 44,000 rows that were
 * reconciled last night are a healthy job *and* a usable listing, and the two
 * can come apart.
 */
export default function AdminStockCodes() {
  const queryClient = useQueryClient()
  const { t } = useI18n()

  const query = useQuery({
    queryKey: ['job', JOB_ID],
    queryFn: () => api.listJobRuns(JOB_ID),
    // A run in progress finishes on the server whether or not this tab is open.
    refetchInterval: 15_000,
  })

  // The listing as the API is actually serving it: the count is read off the
  // in-memory cache, and synced_at is null while it is still twstock's snapshot.
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })

  const run = useMutation({
    mutationFn: () => api.runJob(JOB_ID),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', JOB_ID] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      // The listing itself is about to change: the health counter and every
      // cached search result will be out of date.
      queryClient.invalidateQueries({ queryKey: ['health'] })
      queryClient.invalidateQueries({ queryKey: ['search'] })
    },
  })

  const job = query.data?.job
  const error = query.error ?? run.error

  return (
    <div className="stack">
      <Link to="/admin" className="back-link">
        {t('admin.back')}
      </Link>
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('adminCodes.title')}
        </h2>
        <div className="row wrap" style={{ gap: 10 }}>
          {job?.running && (
            <span className="dim">
              <span className="spinner" />{' '}
              {t('adminCodes.syncing', { seconds: job.expected_seconds })}
            </span>
          )}
          <Link to={`/admin/jobs/${JOB_ID}`} className="btn btn-sm">
            {t('adminCodes.scheduleSettings')}
          </Link>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!job || job.running || run.isPending}
            onClick={() => run.mutate()}
          >
            {t('adminCodes.syncNow')}
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-error">
          {t('adminCodes.opFailed', { message: runErrorMessage(error, t) })}
        </div>
      )}

      {run.data && !run.error && job && (
        <div className="banner banner-ok">
          {t('adminCodes.started', { seconds: job.expected_seconds })}
        </div>
      )}

      {job && !job.schedule.enabled && (
        <div className="banner banner-warn">
          {t('adminCodes.scheduleOffBefore')}
          <Link to={`/admin/jobs/${JOB_ID}`}>{t('adminCodes.scheduleSettings')}</Link>
          {t('adminCodes.scheduleOffAfter')}
        </div>
      )}

      {job && isStale(job) && (
        <div className="banner banner-warn">
          {t('adminCodes.staleNotice', {
            since: sinceLabel(job.last_success_at, t, 'jobs.neverSucceeded'),
          })}
        </div>
      )}

      {health.data && health.data.stock_codes_synced_at === null && (
        <div className="banner banner-warn">{t('adminCodes.neverSyncedNotice')}</div>
      )}

      <section className="card">
        <div className="stat-grid">
          <div>
            <div className="stat-label">{t('adminCodes.statActive')}</div>
            <div className="stat-value">{fmtInt(health.data?.stock_codes_loaded ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">{t('adminCodes.statLastSuccess')}</div>
            <div className="stat-value">
              {sinceLabel(job?.last_success_at, t, 'jobs.neverSucceeded')}
            </div>
          </div>
          <div>
            <div className="stat-label">{t('adminCodes.statSchedule')}</div>
            <div className="stat-value">{job ? scheduleLabel(job, t) : '--'}</div>
          </div>
          <div>
            <div className="stat-label">{t('adminCodes.statNextRun')}</div>
            <div className="stat-value">{job ? untilLabel(job.next_run_at, t) : '--'}</div>
          </div>
          <div>
            <div className="stat-label">{t('adminCodes.statRuns')}</div>
            <div className="stat-value">{fmtInt(query.data?.total ?? null)}</div>
          </div>
        </div>
      </section>

      {query.isPending || !job ? (
        <div className="center-note">
          <span className="spinner" />
        </div>
      ) : (
        <section className="card">
          <JobRunsTable
            job={job}
            runs={query.data!.runs}
            emptyNote={t('adminCodes.noRuns')}
          />

          <p className="dim" style={{ marginTop: 12 }}>
            {t('adminCodes.footer')}
          </p>
        </section>
      )}
    </div>
  )
}
