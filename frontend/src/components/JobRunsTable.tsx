import type { Job, JobRun } from '../api/types'
import { fmtInt } from '../utils/format'
import { STATUS_LABEL, TRIGGER_LABEL, fmtTime, statusClass } from '../utils/jobs'

/** A count that reads as "nothing happened" when it is zero. */
function Delta({ value }: { value: number | undefined }) {
  if (!value) return <span className="dim">—</span>
  return <>{fmtInt(value)}</>
}

/**
 * One table for every job.
 *
 * The columns between 耗時 and 訊息 come from `job.stat_labels`, so a job added
 * to the registry tomorrow -- counting something nobody has thought of yet --
 * renders here without this file changing. That is the whole reason
 * `job_run.stats` is a JSON blob rather than a set of columns.
 */
export default function JobRunsTable({
  job,
  runs,
  emptyNote = '還沒有任何執行紀錄',
}: {
  job: Job
  runs: JobRun[]
  emptyNote?: string
}) {
  const statKeys = Object.keys(job.stat_labels)

  return (
    <>
      <table className="data">
        <thead>
          <tr>
            <th>開始時間</th>
            <th>觸發</th>
            <th>結果</th>
            <th>耗時</th>
            {statKeys.map((key) => (
              <th key={key}>{job.stat_labels[key]}</th>
            ))}
            <th>訊息</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td className="tabular">{fmtTime(run.started_at)}</td>
              <td className="dim">
                {TRIGGER_LABEL[run.trigger]}
                {/* Who pressed it. Only manual runs have one, and it is the
                    point of recording the trigger at all. */}
                {run.actor && ` · ${run.actor}`}
              </td>
              <td className={statusClass(run.status)}>{STATUS_LABEL[run.status]}</td>
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

      {runs.length === 0 && <div className="center-note">{emptyNote}</div>}
    </>
  )
}
