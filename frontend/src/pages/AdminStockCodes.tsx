import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import JobRunsTable from '../components/JobRunsTable'
import { fmtInt } from '../utils/format'
import { isStale, runErrorMessage, scheduleLabel, sinceLabel, untilLabel } from '../utils/jobs'

/** The job this page is about. It is one of several in the registry; this view
 *  exists because the listing has consequences the generic console cannot
 *  explain -- a stale sync means 新掛牌的股票查不到, which is a support ticket,
 *  not a red dot. */
const JOB_ID = 'stock_code_sync'

/**
 * 上市櫃名冊同步 -- the listing sync, with the context that only applies to it.
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
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          上市櫃名冊同步
        </h2>
        <div className="row wrap" style={{ gap: 10 }}>
          {job?.running && (
            <span className="dim">
              <span className="spinner" /> 抓取中，約需 {job.expected_seconds} 秒…
            </span>
          )}
          <Link to={`/admin/jobs/${JOB_ID}`} className="btn btn-sm">
            排程設定
          </Link>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!job || job.running || run.isPending}
            onClick={() => run.mutate()}
          >
            立即同步
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-error">操作失敗：{runErrorMessage(error)}</div>
      )}

      {run.data && !run.error && job && (
        <div className="banner banner-ok">
          已開始同步，約需 {job.expected_seconds} 秒，結果會出現在下面的紀錄裡。
        </div>
      )}

      {job && !job.schedule.enabled && (
        <div className="banner banner-warn">
          這個工作的排程已停用，名冊不會自動更新新掛牌的標的。
          可以到<Link to={`/admin/jobs/${JOB_ID}`}>排程設定</Link>重新啟用。
        </div>
      )}

      {job && isStale(job) && (
        <div className="banner banner-warn">
          已經超過兩個排程週期沒有成功同步（{sinceLabel(job.last_success_at, '從未成功')}）。
          新掛牌的標的目前會查不到，請看下方紀錄的失敗原因。
        </div>
      )}

      {health.data && health.data.stock_codes_synced_at === null && (
        <div className="banner banner-warn">
          名冊還沒跟交易所對過，目前用的是 twstock 內建的快照，會漏掉快照日期之後掛牌的標的。
        </div>
      )}

      <section className="card">
        <div className="stat-grid">
          <div>
            <div className="stat-label">可查詢標的</div>
            <div className="stat-value">{fmtInt(health.data?.stock_codes_loaded ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">最後成功同步</div>
            <div className="stat-value">
              {sinceLabel(job?.last_success_at, '從未成功')}
            </div>
          </div>
          <div>
            <div className="stat-label">排程</div>
            <div className="stat-value">{job ? scheduleLabel(job) : '--'}</div>
          </div>
          <div>
            <div className="stat-label">下次執行</div>
            <div className="stat-value">{job ? untilLabel(job.next_run_at) : '--'}</div>
          </div>
          <div>
            <div className="stat-label">紀錄筆數</div>
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
          <JobRunsTable job={job} runs={query.data!.runs} emptyNote="還沒有任何同步紀錄" />

          <p className="dim" style={{ marginTop: 12 }}>
            只保留最近 200 次。「略過」代表排程醒來時名冊還在間隔內，沒有需要做的事 ——
            它是這個批次工作還活著的心跳。訊息寫著 Partial 的那幾次，是只有一個市場回應，
            寫了拿到的部分但刻意沒有退役任何代碼。下市的標的不會被刪除，仍可用完整代碼查到歷史；
            只有過期超過 30 天的認購(售)權證會被清除。
          </p>
        </section>
      )}
    </div>
  )
}
