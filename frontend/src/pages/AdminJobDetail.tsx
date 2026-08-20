import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import JobRunsTable from '../components/JobRunsTable'
import JobScheduleForm from '../components/JobScheduleForm'
import { translateJobDescription, translateJobName, useI18n } from '../i18n'
import { fmtInt } from '../utils/format'
import {
  runErrorMessage,
  scheduleLabel,
  sinceLabel,
  statusClass,
  statusLabel,
  untilLabel,
} from '../utils/jobs'

/** One job's schedule and its full audit trail. ADMIN only, guarded on both
 *  the route (App.tsx) and every endpoint it calls. */
export default function AdminJobDetail() {
  const { jobId = '' } = useParams()
  const queryClient = useQueryClient()
  const { t } = useI18n()

  const query = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.listJobRuns(jobId, 100),
    refetchInterval: 15_000,
  })

  const run = useMutation({
    mutationFn: () => api.runJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  if (query.isPending) {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }

  if (query.error) {
    return (
      <div className="stack">
        <Link to="/admin/jobs" className="btn btn-sm" style={{ alignSelf: 'flex-start' }}>
          {t('jobs.back')}
        </Link>
        <div className="banner banner-error">
          {t('jobs.loadFailed', { message: (query.error as Error).message })}
        </div>
      </div>
    )
  }

  const { job, total, runs } = query.data!

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <div className="row wrap" style={{ gap: 10 }}>
          <Link to="/admin/jobs" className="btn btn-sm">
            {t('jobs.back')}
          </Link>
          <h2 className="card-title" style={{ margin: 0 }}>
            {translateJobName(job.id, job.name, t)}
          </h2>
          {job.running && (
            <span className="dim">
              <span className="spinner" /> {t('jobs.running')}
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={job.running || run.isPending}
          onClick={() => run.mutate()}
          title={t('jobs.runNowTitle', {
            seconds: job.expected_seconds,
            cooldown: job.manual_cooldown_seconds,
          })}
        >
          {t('jobs.runNow')}
        </button>
      </div>

      <p className="dim" style={{ margin: 0 }}>
        {translateJobDescription(job.id, job.description, t)}
      </p>

      {run.error && (
        <div className="banner banner-error">{runErrorMessage(run.error, t)}</div>
      )}
      {run.data && !run.error && (
        <div className="banner banner-ok">
          {t('jobs.startedBelow', { seconds: job.expected_seconds })}
        </div>
      )}

      <section className="card stack" style={{ gap: 12 }}>
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
                <span className={statusClass(job.last_run.status)}>
                  {statusLabel(job.last_run.status, t)}
                </span>
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
            <div className="stat-value">{fmtInt(total)}</div>
          </div>
        </div>

        <JobScheduleForm job={job} />
      </section>

      <section className="card">
        <JobRunsTable job={job} runs={runs} />
        <p className="dim" style={{ marginTop: 12 }}>
          {t('jobs.detailFooter')}
        </p>
      </section>
    </div>
  )
}
