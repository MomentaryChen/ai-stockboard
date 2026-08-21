import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { Job } from '../api/types'
import JobScheduleForm from '../components/JobScheduleForm'
import {
  translateJobDescription,
  translateJobName,
  useI18n,
} from '../i18n'
import { errorMessage } from '../utils/errors'
import { fmtInt } from '../utils/format'
import {
  isStale,
  runErrorMessage,
  scheduleLabel,
  sinceLabel,
  statusClass,
  statusLabel,
  untilLabel,
} from '../utils/jobs'

/**
 * The background-job console: every job on one screen.
 *
 * Built as a list of jobs rather than a page per job because the question this
 * page answers is "is anything broken?", and that is only answerable by seeing
 * all of them together -- a job that has been failing for a week is invisible
 * if you have to know to go and look at it. Per-job history lives one click
 * away at /admin/jobs/:jobId.
 *
 * Only reachable by an ADMIN (see RequireAuth in App.tsx), and every endpoint
 * behind it enforces that again server-side.
 */

function JobCard({ job }: { job: Job }) {
  const queryClient = useQueryClient()
  const { t } = useI18n()

  const run = useMutation({
    mutationFn: () => api.runJob(job.id),
    // The server answers as soon as the run has *started*, so the interesting
    // state is the next poll, not this response.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })

  const stale = isStale(job)
  const busy = job.running || run.isPending

  return (
    <section className="card stack" style={{ gap: 12 }}>
      <div className="row-between wrap" style={{ gap: 12 }}>
        <div className="row" style={{ gap: 8 }}>
          <span
            className={`dot ${
              !job.schedule.enabled ? '' : stale ? 'dot-bad' : 'dot-ok'
            }`}
            style={!job.schedule.enabled ? { background: 'var(--text-dim)' } : undefined}
            title={
              !job.schedule.enabled
                ? t('jobs.dotDisabled')
                : stale
                  ? t('jobs.dotStale')
                  : t('jobs.dotOk')
            }
          />
          <h3 className="card-title" style={{ margin: 0 }}>
            {translateJobName(job.id, job.name, t)}
          </h3>
          {job.running && (
            <span className="dim">
              <span className="spinner" /> {t('jobs.running')}
            </span>
          )}
        </div>

        <div className="row wrap" style={{ gap: 8 }}>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={busy}
            onClick={() => run.mutate()}
            title={t('jobs.runNowTitle', {
              seconds: job.expected_seconds,
              cooldown: job.manual_cooldown_seconds,
            })}
          >
            {t('jobs.runNow')}
          </button>
          <Link to={`/admin/jobs/${job.id}`} className="btn btn-sm">
            {t('jobs.runHistory')}
          </Link>
        </div>
      </div>

      <p className="dim" style={{ margin: 0 }}>
        {translateJobDescription(job.id, job.description, t)}
      </p>

      {stale && (
        <div className="banner banner-warn">
          {job.last_success_at
            ? t('jobs.staleNoticeSince', { since: sinceLabel(job.last_success_at, t) })
            : t('jobs.staleNotice')}
        </div>
      )}

      {run.error && (
        <div className="banner banner-error">{runErrorMessage(run.error, t)}</div>
      )}
      {run.data && !run.error && (
        <div className="banner banner-ok">
          {t('jobs.started', { seconds: job.expected_seconds })}
        </div>
      )}

      <div className="stat-grid">
        <div>
          <div className="stat-label">{t('jobs.statSchedule')}</div>
          <div className="stat-value">{scheduleLabel(job, t)}</div>
        </div>
        <div>
          <div className="stat-label">{t('jobs.statNextRun')}</div>
          <div className="stat-value">{untilLabel(job.next_run_at, t)}</div>
        </div>
        <div>
          <div className="stat-label">{t('jobs.statLastResult')}</div>
          <div className="stat-value">
            {job.last_run ? (
              <>
                <span className={statusClass(job.last_run.status)}>
                  {statusLabel(job.last_run.status, t)}
                </span>{' '}
                <span className="dim">{sinceLabel(job.last_run.started_at, t)}</span>
              </>
            ) : (
              <span className="dim">{t('jobs.neverRun')}</span>
            )}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('jobs.statLastSuccess')}</div>
          <div className="stat-value">
            {sinceLabel(job.last_success_at, t, 'jobs.neverSucceeded')}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('jobs.statRuns')}</div>
          <div className="stat-value">{fmtInt(job.total_runs)}</div>
        </div>
      </div>

      <JobScheduleForm job={job} />
    </section>
  )
}

export default function AdminJobs() {
  const { t } = useI18n()

  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: api.listJobs,
    // Runs finish on the server whether or not this tab is open, and a manual
    // run reports back by appearing here rather than in its own response.
    refetchInterval: 15_000,
  })

  if (jobs.isPending) {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }

  return (
    <div className="stack">
      <Link to="/admin" className="back-link">
        {t('admin.back')}
      </Link>
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('jobs.title')}
        </h2>
        <span className="dim">{t('jobs.timezone', { timezone: jobs.data?.timezone ?? '--' })}</span>
      </div>

      {jobs.error && (
        <div className="banner banner-error">
          {t('jobs.loadFailed', { message: errorMessage(jobs.error, t) })}
        </div>
      )}

      {jobs.data && !jobs.data.scheduler_enabled && (
        <div className="banner banner-warn">
          {t('jobs.schedulerOffBefore')}
          <code>JOBS_SCHEDULER_ENABLED=false</code>
          {t('jobs.schedulerOffAfter')}
        </div>
      )}

      <div className="job-grid">
        {(jobs.data?.jobs ?? []).map((job) => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>

      <p className="dim">{t('jobs.footer')}</p>
    </div>
  )
}
