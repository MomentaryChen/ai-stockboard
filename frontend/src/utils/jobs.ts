/** Shared vocabulary for the background-job console.
 *
 *  Kept in one place because the overview, the detail page and the listing-sync
 *  page all render the same statuses and schedules, and three copies of the
 *  colour rule is how "成功" ends up green in one of them and red in another.
 */

import { ApiError } from '../api/client'
import type { Job, JobStatus, JobTrigger } from '../api/types'

export const STATUS_LABEL: Record<JobStatus, string> = {
  success: '成功',
  // Not a failure: the job woke up and correctly found nothing to do.
  skipped: '略過',
  failed: '失敗',
}

export const TRIGGER_LABEL: Record<JobTrigger, string> = {
  startup: '啟動',
  schedule: '排程',
  manual: '手動',
}

/** Reuses the price colours: red for trouble, green for a clean run. */
export function statusClass(status: JobStatus): string {
  if (status === 'failed') return 'up'
  if (status === 'success') return 'down'
  return 'dim'
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-TW', { hour12: false })
}

/** '3 分鐘前' / '2 小時前'. Past tense -- for anything already recorded. */
export function sinceLabel(iso: string | null | undefined, empty = '從未執行'): string {
  if (!iso) return empty
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000))
  if (minutes < 1) return '剛剛'
  if (minutes < 60) return `${minutes} 分鐘前`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} 小時前`
  return `${Math.round(hours / 24)} 天前`
}

/** '約 3 分鐘後'. A due time in the past reads as 即將執行, not '-2 分鐘後' --
 *  the thread may be mid-tick, or the run may be waiting on its lock. */
export function untilLabel(iso: string | null): string {
  if (!iso) return '已停用'
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000)
  if (minutes <= 0) return '即將執行'
  if (minutes < 60) return `約 ${minutes} 分鐘後`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `約 ${hours} 小時後`
  return `約 ${Math.round(hours / 24)} 天後`
}

/** '每 6 小時' / '每天 04:10 (Asia/Taipei)' / '已停用'. */
export function scheduleLabel(job: Job): string {
  const { enabled, kind, interval_minutes, daily_at, timezone } = job.schedule
  if (!enabled) return '已停用'
  if (kind === 'daily') return `每天 ${daily_at} (${timezone})`
  if (interval_minutes % 1440 === 0) return `每 ${interval_minutes / 1440} 天`
  if (interval_minutes % 60 === 0) return `每 ${interval_minutes / 60} 小時`
  return `每 ${interval_minutes} 分鐘`
}

/** A job the operator should look at: switched on, but nothing has worked for
 *  more than two of its own cycles. Deliberately measured against the job's own
 *  cadence -- a day of silence is alarming for an hourly job and normal for a
 *  weekly one. */
export function isStale(job: Job): boolean {
  if (!job.schedule.enabled) return false
  const cycleMs =
    (job.schedule.kind === 'daily' ? 1440 : job.schedule.interval_minutes) * 60_000
  if (!job.last_success_at) {
    // Never succeeded. Only worth flagging once it has had a chance to.
    return job.total_runs > 0
  }
  return Date.now() - Date.parse(job.last_success_at) > cycleMs * 2
}

/** What to show when 立即執行 is refused.
 *
 *  The server answers in English (CLAUDE.md: error strings are engineering
 *  communication), but a banner is product copy, and the two states an operator
 *  hits by accident deserve a sentence that tells them what to do next. Anything
 *  else -- a 400 naming the interval limits, an upstream failure -- is shown
 *  verbatim, because paraphrasing it would drop the detail that makes it useful.
 */
export function runErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return '這個工作正在執行中，等它跑完再試'
    if (error.status === 429) return '剛剛才手動執行過，請稍後再試（避免對上游打太兇）'
  }
  return (error as Error).message
}
