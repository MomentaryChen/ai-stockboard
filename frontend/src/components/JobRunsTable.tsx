import type { Job, JobRun } from '../api/types'
import { translateJobStat, useI18n } from '../i18n'
import { fmtInt } from '../utils/format'
import { fmtTime, statusClass, statusLabel, triggerLabel } from '../utils/jobs'

/** A count that reads as "nothing happened" when it is zero. */
function Delta({ value }: { value: number | undefined }) {
  if (!value) return <span className="dim">—</span>
  return <>{fmtInt(value)}</>
}

/**
 * One table for every job.
 *
 * The columns between "duration" and "message" come from `job.stat_labels`, so
 * a job added to the registry tomorrow -- counting something nobody has thought
 * of yet -- renders here without this file changing. That is the whole reason
 * `job_run.stats` is a JSON blob rather than a set of columns.
 *
 * The catalogue translates those headers by their *stats key*, falling back to
 * the label the server sent, which keeps that property intact: a brand new job
 * renders in the server's words rather than not at all.
 */
export default function JobRunsTable({
  job,
  runs,
  emptyNote,
}: {
  job: Job
  runs: JobRun[]
  emptyNote?: string
}) {
  const { intlTag, t } = useI18n()
  const statKeys = Object.keys(job.stat_labels)

  return (
    <>
      <table className="data">
        <thead>
          <tr>
            <th>{t('jobRuns.colStarted')}</th>
            <th>{t('jobRuns.colTrigger')}</th>
            <th>{t('jobRuns.colResult')}</th>
            <th>{t('jobRuns.colDuration')}</th>
            {statKeys.map((key) => (
              <th key={key}>{translateJobStat(key, job.stat_labels[key], t)}</th>
            ))}
            <th>{t('jobRuns.colMessage')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td className="tabular">{fmtTime(run.started_at, intlTag)}</td>
              <td className="dim">
                {triggerLabel(run.trigger, t)}
                {/* Who pressed it. Only manual runs have one, and it is the
                    point of recording the trigger at all. */}
                {run.actor && ` · ${run.actor}`}
              </td>
              <td className={statusClass(run.status)}>{statusLabel(run.status, t)}</td>
              <td className="tabular dim">{run.duration_seconds}s</td>
              {statKeys.map((key) => (
                <td key={key} className="tabular">
                  <Delta value={run.stats[key]} />
                </td>
              ))}
              <td className="dim">{run.message ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {runs.length === 0 && (
        <div className="center-note">{emptyNote ?? t('jobRuns.empty')}</div>
      )}
    </>
  )
}
