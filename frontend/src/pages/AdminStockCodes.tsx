import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { SyncRun, SyncStatus, SyncTrigger } from '../api/types'
import { fmtInt } from '../utils/format'

/** Roughly how long a forced run takes: the 上市 page alone is ~8 MB. */
const EXPECTED_SYNC_SECONDS = 45

const STATUS_LABEL: Record<SyncStatus, string> = {
  synced: '已同步',
  skipped: '略過',
  failed: '失敗',
}

const TRIGGER_LABEL: Record<SyncTrigger, string> = {
  startup: '啟動',
  schedule: '排程',
  manual: '手動',
}

/** Reuses the price colours: red for trouble, green for a clean run. */
function statusClass(status: SyncStatus): string {
  if (status === 'failed') return 'up'
  if (status === 'synced') return 'down'
  return 'dim'
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-TW', { hour12: false })
}

function sinceLabel(iso: string | null): string {
  if (!iso) return '從未同步'
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000))
  if (minutes < 60) return `${minutes} 分鐘前`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} 小時前`
  return `${Math.round(hours / 24)} 天前`
}

/** A count that reads as "nothing happened" when it is zero. */
function Delta({ value }: { value: number }) {
  if (value === 0) return <span className="dim">—</span>
  return <>{fmtInt(value)}</>
}

export default function AdminStockCodes() {
  const queryClient = useQueryClient()

  const runs = useQuery({
    queryKey: ['sync-runs'],
    queryFn: () => api.listSyncRuns(),
    // A run in progress finishes on the server whether or not this tab is open,
    // so poll rather than leaving the table stale behind a manual refresh.
    refetchInterval: 30_000,
  })

  const sync = useMutation({
    mutationFn: () => api.syncStockCodes(true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sync-runs'] })
      // The listing itself just changed: the health counter and every cached
      // search result are now out of date.
      queryClient.invalidateQueries({ queryKey: ['health'] })
      queryClient.invalidateQueries({ queryKey: ['search'] })
    },
  })

  const data = runs.data
  const error = runs.error ?? sync.error

  // A job that has not landed a successful run in more than two intervals is
  // the case this page exists to make visible.
  const staleAfterHours = (data?.interval_hours ?? 24) * 2
  const isStale =
    !!data &&
    data.enabled &&
    (!data.last_success_at ||
      Date.now() - Date.parse(data.last_success_at) > staleAfterHours * 3_600_000)

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          上市櫃名冊同步
        </h2>
        <div className="row wrap" style={{ gap: 10 }}>
          {sync.isPending && (
            <span className="dim">
              <span className="spinner" /> 抓取中，約需 {EXPECTED_SYNC_SECONDS} 秒…
            </span>
          )}
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={sync.isPending}
            onClick={() => sync.mutate()}
          >
            立即同步
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-error">操作失敗：{(error as Error).message}</div>
      )}

      {data && !data.enabled && (
        <div className="banner banner-warn">
          排程同步已關閉（<code>STOCK_CODE_SYNC_ENABLED=false</code>），名冊不會自動更新新掛牌的標的。
        </div>
      )}

      {isStale && (
        <div className="banner banner-warn">
          已超過 {staleAfterHours} 小時沒有成功的同步（{sinceLabel(data?.last_success_at ?? null)}）。
          新掛牌的標的目前會查不到，請看下方紀錄的失敗原因。
        </div>
      )}

      {data && data.synced_at === null && (
        <div className="banner banner-warn">
          名冊還沒跟交易所對過，目前用的是 twstock 內建的快照，會漏掉快照日期之後掛牌的標的。
        </div>
      )}

      {sync.data && (
        <div
          className={`banner ${
            sync.data.status === 'failed' ? 'banner-error' : 'banner-ok'
          }`}
        >
          本次同步：{STATUS_LABEL[sync.data.status]} · 新增 {fmtInt(sync.data.inserted)} ·
          更新 {fmtInt(sync.data.updated)} · 下市 {fmtInt(sync.data.delisted)} ·
          清除過期權證 {fmtInt(sync.data.pruned)}
          {sync.data.message && ` · ${sync.data.message}`}
        </div>
      )}

      <section className="card">
        <div className="stat-grid">
          <div>
            <div className="stat-label">可查詢標的</div>
            <div className="stat-value">{fmtInt(data?.active ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">最後成功同步</div>
            <div className="stat-value">{sinceLabel(data?.last_success_at ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">排程</div>
            <div className="stat-value">
              {data?.enabled ? `每 ${data.interval_hours} 小時` : '已關閉'}
            </div>
          </div>
          <div>
            <div className="stat-label">紀錄筆數</div>
            <div className="stat-value">{fmtInt(data?.total ?? null)}</div>
          </div>
        </div>
      </section>

      {runs.isPending ? (
        <div className="center-note">
          <span className="spinner" />
        </div>
      ) : (
        <section className="card">
          <table className="data">
            <thead>
              <tr>
                <th>開始時間</th>
                <th>來源</th>
                <th>結果</th>
                <th>耗時</th>
                <th>新增</th>
                <th>更新</th>
                <th>下市</th>
                <th>清除</th>
                <th>可查詢</th>
                <th>訊息</th>
              </tr>
            </thead>
            <tbody>
              {(data?.runs ?? []).map((run: SyncRun) => (
                <tr key={run.id}>
                  <td className="tabular">{fmtTime(run.started_at)}</td>
                  <td className="dim">{TRIGGER_LABEL[run.trigger]}</td>
                  <td className={statusClass(run.status)}>
                    {STATUS_LABEL[run.status]}
                    {/* One market of two means the run wrote what it got but
                        deliberately retired nothing. */}
                    {run.status === 'synced' && run.sources.length === 1 && ' (部分)'}
                  </td>
                  <td className="tabular dim">{run.duration_seconds}s</td>
                  <td className="tabular">
                    <Delta value={run.inserted} />
                  </td>
                  <td className="tabular">
                    <Delta value={run.updated} />
                  </td>
                  <td className="tabular">
                    <Delta value={run.delisted} />
                  </td>
                  <td className="tabular">
                    <Delta value={run.pruned} />
                  </td>
                  <td className="tabular dim">{fmtInt(run.active)}</td>
                  <td className="dim">{run.message ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {data?.runs.length === 0 && (
            <div className="center-note">還沒有任何同步紀錄</div>
          )}

          <p className="dim" style={{ marginTop: 12 }}>
            只保留最近 200 次。「略過」代表排程醒來時名冊還在間隔內，沒有需要做的事 ——
            它是這個批次工作還活著的心跳。下市的標的不會被刪除，仍可用完整代碼查到歷史；
            只有過期超過 30 天的認購(售)權證會被清除。
          </p>
        </section>
      )}
    </div>
  )
}
