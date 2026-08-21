import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Job, ScheduleKind } from '../api/types'
import { useI18n, type MessageKey, type Translate } from '../i18n'
import { fmtTime } from '../utils/jobs'

type Unit = 'minutes' | 'hours' | 'days'

const UNIT_MINUTES: Record<Unit, number> = { minutes: 1, hours: 60, days: 1440 }
const UNIT_KEY: Record<Unit, MessageKey> = {
  minutes: 'schedule.unitMinutes',
  hours: 'schedule.unitHours',
  days: 'schedule.unitDays',
}

/** Show 1440 minutes as 1 day. Picks the largest unit that divides evenly, so
 *  what the operator typed is what they see when they come back. */
function splitInterval(minutes: number): { value: number; unit: Unit } {
  if (minutes % 1440 === 0) return { value: minutes / 1440, unit: 'days' }
  if (minutes % 60 === 0) return { value: minutes / 60, unit: 'hours' }
  return { value: minutes, unit: 'minutes' }
}

function rangeHint(minMinutes: number, maxMinutes: number, t: Translate): string {
  const format = (minutes: number) => {
    const { value, unit } = splitInterval(minutes)
    return t('schedule.amount', { value, unit: t(UNIT_KEY[unit]) })
  }
  return t('schedule.rangeHint', { min: format(minMinutes), max: format(maxMinutes) })
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
  const { intlTag, t } = useI18n()
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
          title={
            schedule.enabled ? t('schedule.disableTitle') : t('schedule.enableTitle')
          }
        >
          {schedule.enabled ? t('schedule.enabled') : t('schedule.disabled')}
        </button>

        <div className="segmented">
          {(['interval', 'daily'] as ScheduleKind[]).map((option) => (
            <button
              key={option}
              type="button"
              className={`btn btn-sm ${kind === option ? 'active' : ''}`}
              onClick={() => setKind(option)}
            >
              {option === 'interval'
                ? t('schedule.kindInterval')
                : t('schedule.kindDaily')}
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
              style={{ maxWidth: 110 }}
              value={unit}
              onChange={(event) => setUnit(event.target.value as Unit)}
            >
              {(['minutes', 'hours', 'days'] as Unit[]).map((option) => (
                <option key={option} value={option}>
                  {t(UNIT_KEY[option])}
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
          {t('schedule.save')}
        </button>
      </div>

      <div className="dim">
        {kind === 'interval'
          ? rangeHint(schedule.min_interval_minutes, schedule.max_interval_minutes, t)
          : t('schedule.timezoneNote', { timezone: schedule.timezone })}
        {schedule.is_default
          ? t('schedule.defaultNote')
          : schedule.updated_by &&
            t('schedule.updatedBy', {
              user: schedule.updated_by,
              time: fmtTime(schedule.updated_at!, intlTag),
            })}
      </div>

      {outOfRange && (
        <div className="banner banner-warn">{t('schedule.outOfRange')}</div>
      )}

      {save.error && (
        <div className="banner banner-error">
          {t('schedule.saveFailed', { message: (save.error as Error).message })}
        </div>
      )}
    </div>
  )
}
