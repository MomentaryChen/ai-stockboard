/** Shared vocabulary for the background-job console.
 *
 *  Kept in one place because the overview, the detail page and the listing-sync
 *  page all render the same statuses and schedules, and three copies of the
 *  colour rule is how "success" ends up green in one of them and red in another.
 *
 *  Everything that produces a sentence takes `t` rather than reading a catalogue
 *  itself: these are plain functions, not components, so they have no context to
 *  read -- and passing it in keeps them pure and testable.
 */

import { ApiError } from '../api/client'
import type { Job, JobStatus, JobTrigger } from '../api/types'
import type { MessageKey, Translate } from '../i18n'

const STATUS_KEY: Record<JobStatus, MessageKey> = {
  success: 'jobs.statusSuccess',
  // Not a failure: the job woke up and correctly found nothing to do.
  skipped: 'jobs.statusSkipped',
  failed: 'jobs.statusFailed',
}

const TRIGGER_KEY: Record<JobTrigger, MessageKey> = {
  startup: 'jobs.triggerStartup',
  schedule: 'jobs.triggerSchedule',
  manual: 'jobs.triggerManual',
}

export function statusLabel(status: JobStatus, t: Translate): string {
  return t(STATUS_KEY[status])
}

export function triggerLabel(trigger: JobTrigger, t: Translate): string {
  return t(TRIGGER_KEY[trigger])
}

/** Reuses the price colours: red for trouble, green for a clean run. */
export function statusClass(status: JobStatus): string {
  if (status === 'failed') return 'up'
  if (status === 'success') return 'down'
  return 'dim'
}

export function fmtTime(iso: string, intlTag: string): string {
  return new Date(iso).toLocaleString(intlTag, { hour12: false })
}

/** '3 minutes ago' / '2 hours ago'. Past tense -- for anything already recorded. */
export function sinceLabel(
  iso: string | null | undefined,
  t: Translate,
  empty: MessageKey = 'jobs.neverRun',
): string {
  if (!iso) return t(empty)
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000))
  if (minutes < 1) return t('jobs.justNow')
  if (minutes < 60) return t('jobs.minutesAgo', { count: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 48) return t('jobs.hoursAgo', { count: hours })
  return t('jobs.daysAgo', { count: Math.round(hours / 24) })
}

/** 'in about 3 minutes'. A due time in the past reads as "due now", not
 *  "-2 minutes" -- the thread may be mid-tick, or the run may be waiting on
 *  its lock. */
export function untilLabel(iso: string | null, t: Translate): string {
  if (!iso) return t('jobs.scheduleOff')
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000)
  if (minutes <= 0) return t('jobs.dueNow')
  if (minutes < 60) return t('jobs.inMinutes', { count: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 48) return t('jobs.inHours', { count: hours })
  return t('jobs.inDays', { count: Math.round(hours / 24) })
}

/** 'every 6 hours' / 'daily at 04:10 (Asia/Taipei)' / 'Disabled'. */
export function scheduleLabel(job: Job, t: Translate): string {
  const { enabled, kind, interval_minutes, daily_at, timezone } = job.schedule
  if (!enabled) return t('jobs.scheduleOff')
  if (kind === 'daily') return t('jobs.dailyAt', { time: daily_at, timezone })
  if (interval_minutes % 1440 === 0) {
    return t('jobs.everyDays', { count: interval_minutes / 1440 })
  }
  if (interval_minutes % 60 === 0) {
    return t('jobs.everyHours', { count: interval_minutes / 60 })
  }
  return t('jobs.everyMinutes', { count: interval_minutes })
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

/** What to show when "run now" is refused.
 *
 *  The server answers in English (CLAUDE.md: error strings are engineering
 *  communication), but a banner is product copy, and the two states an operator
 *  hits by accident deserve a sentence that tells them what to do next -- in
 *  their own language. Anything else -- a 400 naming the interval limits, an
 *  upstream failure -- is shown verbatim, because paraphrasing it would drop the
 *  detail that makes it useful.
 */
export function runErrorMessage(error: unknown, t: Translate): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return t('jobs.errorRunning')
    if (error.status === 429) return t('jobs.errorCooldown')
  }
  return (error as Error).message
}
