import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type {
  EngineRegion,
  EngineTrackCall,
  EngineTrackConclusion,
  EngineTrackHold,
  EngineTrackSummary,
} from '../api/types'
import { useI18n, type MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'

/**
 * Whether the advice on the other pages turned out to be any good.
 *
 * It starts out mostly empty and says so. Showing early averages would invite
 * exactly the conclusion they cannot support, so the server withholds them until
 * enough separate days have matured.
 */

const CALL_KEY: Record<EngineTrackCall['call'], MessageKey> = {
  buy_now: 'engine.callBuyNow',
  buy_dip: 'engine.callBuyDip',
  no_buy: 'engine.callNoBuy',
}

const HOLD_KEY: Record<EngineTrackHold['hold_state'], MessageKey> = {
  strong: 'engine.holdStrong',
  unclear: 'engine.holdUnclear',
  weak: 'engine.holdWeak',
}

const VERDICT_KEY: Record<EngineTrackConclusion, MessageKey> = {
  collecting: 'engine.verdictCollecting',
  beat: 'engine.verdictBeat',
  lagged: 'engine.verdictLagged',
  no_difference: 'engine.verdictNoDifference',
}

function signed(value: number | null): string {
  return value === null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`
}

function share(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(0)}%`
}

function TrackTable({
  heading,
  labelHeader,
  benchmark,
  rows,
}: {
  heading: string
  labelHeader: string
  benchmark: string
  rows: Array<{ label: string; summary: EngineTrackSummary }>
}) {
  const { t } = useI18n()
  return (
    <section className="card">
      <h2 className="card-title">{heading}</h2>
      <div className="engine-track-scroll">
        <table className="engine-track-table">
          <thead>
            <tr>
              <th>{labelHeader}</th>
              <th>{t('engine.trackDays')}</th>
              <th>{t('engine.trackCalls')}</th>
              <th>{t('engine.trackReturn')}</th>
              <th>{t('engine.trackWin')}</th>
              <th>{t('engine.trackExcess', { benchmark })}</th>
              <th>{t('engine.trackVerdict')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ label, summary }) => (
              <tr key={label}>
                <td>{label}</td>
                <td>{summary.call_dates}</td>
                <td>{summary.calls}</td>
                <td>{signed(summary.avg_return)}</td>
                <td>{share(summary.win_rate)}</td>
                <td>{signed(summary.avg_excess)}</td>
                <td>{t(VERDICT_KEY[summary.conclusion], { benchmark })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function EngineTrackRecord({ region }: { region: EngineRegion }) {
  const { t } = useI18n()
  const record = useQuery({
    queryKey: ['engine', 'track-record', region],
    queryFn: () => api.getEngineTrackRecord(region),
  })
  const benchmark = region === 'us' ? 'SPY' : '0050'

  if (record.isLoading) {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }
  if (record.error) return <div className="center-note">{errorMessage(record.error, t)}</div>
  const data = record.data
  if (!data) return null

  const callRows = (horizon: EngineTrackCall['horizon']) =>
    data.calls
      .filter((row) => row.horizon === horizon)
      .map((row) => ({ label: t(CALL_KEY[row.call]), summary: row }))

  return (
    <div className="stack engine-main-column">
      <div>
        <h1 className="engine-title">{t('engine.trackTitle')}</h1>
        <p className="engine-note">{t('engine.trackIntro', { benchmark })}</p>
      </div>
      {data.graded === 0 ? (
        <p className="engine-warning">{t('engine.trackEmpty')}</p>
      ) : (
        <>
          <p className="dim">
            {t('engine.trackRange', {
              count: data.graded,
              first: data.first_call ?? '',
              last: data.last_call ?? '',
            })}{' '}
            {t('engine.trackMinDates', { count: data.min_call_dates })}
          </p>
          <TrackTable heading={t('engine.trackShortHeading')} labelHeader={t('engine.trackCall')} benchmark={benchmark} rows={callRows('short')} />
          <TrackTable heading={t('engine.trackLongHeading')} labelHeader={t('engine.trackCall')} benchmark={benchmark} rows={callRows('long')} />
          <TrackTable
            heading={t('engine.trackHoldHeading')}
            labelHeader={t('engine.trackHold')}
            benchmark={benchmark}
            rows={data.holds.map((row) => ({ label: t(HOLD_KEY[row.hold_state]), summary: row }))}
          />
          <p className="dim">{t('engine.trackFootnote')}</p>
        </>
      )}
    </div>
  )
}
