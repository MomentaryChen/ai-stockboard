import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import JobRunsTable from '../components/JobRunsTable'
import JobScheduleForm from '../components/JobScheduleForm'
import { fmtInt } from '../utils/format'
import {
  STATUS_LABEL,
  runErrorMessage,
  scheduleLabel,
  sinceLabel,
  statusClass,
  untilLabel,
} from '../utils/jobs'

/** One job's schedule and its full audit trail. ADMIN only, guarded on both
 *  the route (App.tsx) and every endpoint it calls. */
export default function AdminJobDetail() {
  const { jobId = '' } = useParams()
  const queryClient = useQueryClient()

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
          ← 排程作業
        </Link>
        <div className="banner banner-error">讀取失敗：{(query.error as Error).message}</div>
      </div>
    )
  }

  const { job, total, runs } = query.data!

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <div className="row wrap" style={{ gap: 10 }}>
          <Link to="/admin/jobs" className="btn btn-sm">
            ← 排程作業
          </Link>
          <h2 className="card-title" style={{ margin: 0 }}>
            {job.name}
          </h2>
          {job.running && (
            <span className="dim">
              <span className="spinner" /> 執行中
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={job.running || run.isPending}
          onClick={() => run.mutate()}
          title={`預計約 ${job.expected_seconds} 秒`}
        >
          立即執行
        </button>
      </div>

      <p className="dim" style={{ margin: 0 }}>
        {job.description}
      </p>

      {run.error && (
        <div className="banner banner-error">{runErrorMessage(run.error)}</div>
      )}
      {run.data && !run.error && (
        <div className="banner banner-ok">{`已開始執行，約需 ${job.expected_seconds} 秒，跑完會出現在下面的紀錄裡`}</div>
      )}

      <section className="card stack" style={{ gap: 12 }}>
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
                <span className={statusClass(job.last_run.status)}>
                  {STATUS_LABEL[job.last_run.status]}
                </span>
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
            <div className="stat-value">{fmtInt(total)}</div>
          </div>
        </div>

        <JobScheduleForm job={job} />
      </section>

      <section className="card">
        <JobRunsTable job={job} runs={runs} />
        <p className="dim" style={{ marginTop: 12 }}>
          每個工作只保留最近 200 次執行。手動執行會記下是哪個管理員按的。
        </p>
      </section>
    </div>
  )
}
