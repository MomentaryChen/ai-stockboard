import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { Job } from '../api/types'
import JobScheduleForm from '../components/JobScheduleForm'
import { fmtInt } from '../utils/format'
import {
  STATUS_LABEL,
  isStale,
  runErrorMessage,
  scheduleLabel,
  sinceLabel,
  statusClass,
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
              !job.schedule.enabled ? '排程已停用' : stale ? '太久沒有成功執行' : '正常'
            }
          />
          <h3 className="card-title" style={{ margin: 0 }}>
            {job.name}
          </h3>
          {job.running && (
            <span className="dim">
              <span className="spinner" /> 執行中
            </span>
          )}
        </div>

        <div className="row wrap" style={{ gap: 8 }}>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={busy}
            onClick={() => run.mutate()}
            title={`預計約 ${job.expected_seconds} 秒；同一個工作 ${job.manual_cooldown_seconds} 秒內只能手動執行一次`}
          >
            立即執行
          </button>
          <Link to={`/admin/jobs/${job.id}`} className="btn btn-sm">
            執行紀錄
          </Link>
        </div>
      </div>

      <p className="dim" style={{ margin: 0 }}>
        {job.description}
      </p>

      {stale && (
        <div className="banner banner-warn">
          已經超過兩個排程週期沒有成功執行
          {job.last_success_at ? `（最後一次 ${sinceLabel(job.last_success_at)}）` : ''}
          ，請看執行紀錄裡的失敗原因。
        </div>
      )}

      {run.error && (
        <div className="banner banner-error">{runErrorMessage(run.error)}</div>
      )}
      {run.data && !run.error && (
        <div className="banner banner-ok">{`已開始執行，約需 ${job.expected_seconds} 秒，結果會出現在執行紀錄裡`}</div>
      )}

      <div className="stat-grid">
        <div>
          <div className="stat-label">排程</div>
          <div className="stat-value">{scheduleLabel(job)}</div>
        </div>
        <div>
          <div className="stat-label">下次執行</div>
          <div className="stat-value">{untilLabel(job.next_run_at)}</div>
        </div>
        <div>
          <div className="stat-label">上次結果</div>
          <div className="stat-value">
            {job.last_run ? (
              <>
                <span className={statusClass(job.last_run.status)}>
                  {STATUS_LABEL[job.last_run.status]}
                </span>{' '}
                <span className="dim">{sinceLabel(job.last_run.started_at)}</span>
              </>
            ) : (
              <span className="dim">從未執行</span>
            )}
          </div>
        </div>
        <div>
          <div className="stat-label">最後成功</div>
          <div className="stat-value">{sinceLabel(job.last_success_at, '從未成功')}</div>
        </div>
        <div>
          <div className="stat-label">紀錄筆數</div>
          <div className="stat-value">{fmtInt(job.total_runs)}</div>
        </div>
      </div>

      <JobScheduleForm job={job} />
    </section>
  )
}

export default function AdminJobs() {
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
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          排程作業
        </h2>
        <span className="dim">時區 {jobs.data?.timezone}</span>
      </div>

      {jobs.error && (
        <div className="banner banner-error">讀取失敗：{(jobs.error as Error).message}</div>
      )}

      {jobs.data && !jobs.data.scheduler_enabled && (
        <div className="banner banner-warn">
          排程器沒有在這個行程裡執行（<code>JOBS_SCHEDULER_ENABLED=false</code>），
          下面所有工作都只能手動執行。多開副本時只讓其中一個開排程是正常設定。
        </div>
      )}

      <div className="job-grid">
        {(jobs.data?.jobs ?? []).map((job) => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>

      <p className="dim">
        「略過」代表工作醒來後確認沒事可做 —— 它是排程還活著的心跳，不是失敗。
        手動執行會記在紀錄裡，並寫下是誰按的。
      </p>
    </div>
  )
}
