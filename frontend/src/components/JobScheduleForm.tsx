import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Job, ScheduleKind } from '../api/types'
import { fmtTime } from '../utils/jobs'

type Unit = 'minutes' | 'hours' | 'days'

const UNIT_MINUTES: Record<Unit, number> = { minutes: 1, hours: 60, days: 1440 }
const UNIT_LABEL: Record<Unit, string> = { minutes: '分鐘', hours: '小時', days: '天' }

/** Show 1440 分鐘 as 1 天. Picks the largest unit that divides evenly, so what
 *  the operator typed is what they see when they come back. */
function splitInterval(minutes: number): { value: number; unit: Unit } {
  if (minutes % 1440 === 0) return { value: minutes / 1440, unit: 'days' }
  if (minutes % 60 === 0) return { value: minutes / 60, unit: 'hours' }
  return { value: minutes, unit: 'minutes' }
}

function rangeHint(minMinutes: number, maxMinutes: number): string {
  const format = (minutes: number) => {
    const { value, unit } = splitInterval(minutes)
    return `${value} ${UNIT_LABEL[unit]}`
  }
  return `允許範圍 ${format(minMinutes)} ～ ${format(maxMinutes)}`
}

/**
 * The schedule editor. Every field here is something an admin owns; the bounds
 * around them are not.
 *
 * The form disables what the server would refuse, but it is not the guard: the
 * API validates the merged schedule against the job's own floor and ceiling
 * whatever arrives. A rejection comes back as a 400 and is shown verbatim
 * rather than paraphrased, because the message names the actual limit.
 */
export default function JobScheduleForm({ job }: { job: Job }) {
  const queryClient = useQueryClient()
  const { schedule } = job

  const initial = splitInterval(schedule.interval_minutes)
  const [kind, setKind] = useState<ScheduleKind>(schedule.kind)
  const [value, setValue] = useState(String(initial.value))
  const [unit, setUnit] = useState<Unit>(initial.unit)
  const [dailyAt, setDailyAt] = useState(schedule.daily_at)

  // Re-seed when the job is refetched (someone else's edit, or our own save
  // coming back). Keyed on the fields themselves so typing is never clobbered
  // by a poll that changed nothing.
  useEffect(() => {
    const next = splitInterval(schedule.interval_minutes)
    setKind(schedule.kind)
    setValue(String(next.value))
    setUnit(next.unit)
    setDailyAt(schedule.daily_at)
  }, [schedule.kind, schedule.interval_minutes, schedule.daily_at])

  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.updateJobSchedule>[1]) =>
      api.updateJobSchedule(job.id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['job', job.id] })
    },
  })

  const minutes = Math.round((Number(value) || 0) * UNIT_MINUTES[unit])
  const outOfRange =
    kind === 'interval' &&
    (minutes < schedule.min_interval_minutes || minutes > schedule.max_interval_minutes)

  const dirty =
    kind !== schedule.kind ||
    (kind === 'interval' && minutes !== schedule.interval_minutes) ||
    (kind === 'daily' && dailyAt !== schedule.daily_at)

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <button
          type="button"
          className={`btn btn-sm ${schedule.enabled ? 'active' : ''}`}
          disabled={save.isPending}
          onClick={() => save.mutate({ enabled: !schedule.enabled })}
          title={schedule.enabled ? '停用後這個工作不會自動執行' : '重新啟用排程'}
        >
          {schedule.enabled ? '排程啟用中' : '排程已停用'}
        </button>

        <div className="segmented">
          {(['interval', 'daily'] as ScheduleKind[]).map((option) => (
            <button
              key={option}
              type="button"
              className={`btn btn-sm ${kind === option ? 'active' : ''}`}
              onClick={() => setKind(option)}
            >
              {option === 'interval' ? '固定間隔' : '每天定時'}
            </button>
          ))}
        </div>

        {kind === 'interval' ? (
          <>
            <input
              className="text-input tabular"
              style={{ maxWidth: 90 }}
              type="number"
              min={1}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
            <select
              className="text-input"
              style={{ maxWidth: 90 }}
              value={unit}
              onChange={(event) => setUnit(event.target.value as Unit)}
            >
              {(['minutes', 'hours', 'days'] as Unit[]).map((option) => (
                <option key={option} value={option}>
                  {UNIT_LABEL[option]}
                </option>
              ))}
            </select>
          </>
        ) : (
          <input
            className="text-input tabular"
            style={{ maxWidth: 120 }}
            type="time"
            value={dailyAt}
            onChange={(event) => setDailyAt(event.target.value)}
          />
        )}

        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={!dirty || outOfRange || save.isPending}
          onClick={() =>
            save.mutate(
              kind === 'interval'
                ? { kind, interval_minutes: minutes }
                : { kind, daily_at: dailyAt },
            )
          }
        >
          儲存排程
        </button>
      </div>

      <div className="dim">
        {kind === 'interval'
          ? rangeHint(schedule.min_interval_minutes, schedule.max_interval_minutes)
          : `以 ${schedule.timezone} 為準`}
        {schedule.is_default
          ? ' · 目前是環境變數給的預設值，存檔後就以這裡為準'
          : schedule.updated_by &&
            ` · 上次由 ${schedule.updated_by} 於 ${fmtTime(schedule.updated_at!)} 修改`}
      </div>

      {outOfRange && (
        <div className="banner banner-warn">
          這個間隔超出允許範圍，伺服器會拒絕。上市櫃名冊那類會打交易所的工作，間隔太短會被對方封鎖。
        </div>
      )}

      {save.error && (
        <div className="banner banner-error">
          排程沒有存成功：{(save.error as Error).message}
        </div>
      )}
    </div>
  )
}
