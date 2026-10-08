import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { ScoreStudyBand, ScoreStudySuitability } from '../api/types'
import { useI18n, type MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'
import { direction, fmtInt, fmtSigned } from '../utils/format'

/**
 * Did a high 存股 score get followed by a better holding outcome?
 *
 * One date for the whole universe, then one forward window, grouped by the
 * band the checklist would have assigned that day. Medians stay blank until
 * the price backfill reports nothing left: a sample of whichever names were
 * opened is a record of browsing, and a median over it would read as a
 * finding.
 */

const SUITABILITY_KEY: Record<ScoreStudySuitability, MessageKey> = {
  strong: 'hold.suitStrong',
  ok: 'hold.suitOk',
  weak: 'hold.suitWeak',
  avoid: 'hold.suitAvoid',
}

function pct(value: number | null): string {
  return value === null ? '—' : `${fmtSigned(value)}%`
}

function points(value: number | null): string {
  return value === null ? '—' : `${fmtSigned(value)} pp`
}

function Cell({ value, text }: { value: number | null; text: string }) {
  return <td className={value === null ? undefined : direction(value)}>{text}</td>
}

function BandRow({ band }: { band: ScoreStudyBand }) {
  const { t } = useI18n()
  return (
    <tr>
      <td>{t(SUITABILITY_KEY[band.suitability])}</td>
      <td>{fmtInt(band.count)}</td>
      <Cell value={band.median_total_return_pct} text={pct(band.median_total_return_pct)} />
      <Cell
        value={band.median_annualised_return_pct}
        text={pct(band.median_annualised_return_pct)}
      />
      <Cell
        value={band.median_excess_price_return_pp}
        text={points(band.median_excess_price_return_pp)}
      />
      <Cell value={band.median_max_drawdown_pct} text={pct(band.median_max_drawdown_pct)} />
    </tr>
  )
}

export default function AdminScoreStudy() {
  const { t } = useI18n()
  const study = useQuery({
    queryKey: ['score-study'],
    queryFn: api.getScoreStudy,
  })
  const data = study.data
  const tooSmall = data?.sample_ready && data.bands.some((band) => band.withheld === 'bucket_too_small')

  return (
    <div className="stack">
      <Link to="/admin" className="btn btn-sm" style={{ alignSelf: 'flex-start' }}>
        {t('admin.back')}
      </Link>

      <div className="stack" style={{ gap: 4 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('adminScore.title')}
        </h2>
        <p className="dim" style={{ margin: 0 }}>
          {t('adminScore.lede')}
        </p>
      </div>

      <section className="card stack" style={{ gap: 14 }}>
        {study.isLoading && <p className="dim">{t('adminScore.loading')}</p>}
        {study.isError && (
          <p className="up">{t('adminScore.loadFailed', { message: errorMessage(study.error, t) })}</p>
        )}

        {data && (
          <>
            {!data.sample_ready && (
              <div className="stack" style={{ gap: 8 }}>
                <p className="up" style={{ margin: 0 }}>
                  {t('adminScore.notReady', { remaining: data.remaining })}
                </p>
                <Link to="/admin/jobs" className="btn btn-sm" style={{ alignSelf: 'flex-start' }}>
                  {t('adminScore.openJobs')}
                </Link>
              </div>
            )}

            <p style={{ margin: 0 }}>
              {t('adminScore.window', {
                start: data.as_of,
                end: data.horizon_end,
                years: data.horizon_years,
              })}
            </p>
            <p className="dim" style={{ margin: 0 }}>
              {t('adminScore.universe', {
                universe: fmtInt(data.universe_size),
                included: fmtInt(data.included),
              })}
            </p>
            <p className="dim" style={{ margin: 0 }}>
              {t('adminScore.excluded', {
                noEntry: fmtInt(data.excluded_no_entry),
                pending: fmtInt(data.excluded_pending),
                thinOutcome: fmtInt(data.excluded_thin_outcome),
                thinScore: fmtInt(data.excluded_thin_score),
              })}
            </p>

            <table className="data">
              <thead>
                <tr>
                  <th>{t('adminScore.band')}</th>
                  <th>{t('adminScore.count')}</th>
                  <th>{t('adminScore.total')}</th>
                  <th>{t('adminScore.annualised')}</th>
                  <th>{t('adminScore.excess')}</th>
                  <th>{t('adminScore.drawdown')}</th>
                </tr>
              </thead>
              <tbody>
                {data.bands.map((band) => (
                  <BandRow key={band.suitability} band={band} />
                ))}
              </tbody>
            </table>

            {tooSmall && (
              <p className="dim" style={{ margin: 0 }}>
                {t('adminScore.tooSmall', { min: data.min_bucket })}
              </p>
            )}

            <div className="stack dim" style={{ gap: 6, fontSize: '0.9rem' }}>
              <p style={{ margin: 0 }}>{t('adminScore.noteCheap')}</p>
              <p style={{ margin: 0 }}>{t('adminScore.notePending')}</p>
              <p style={{ margin: 0 }}>{t('adminScore.noteExcess')}</p>
              <p style={{ margin: 0 }}>{t('adminScore.noteCosts')}</p>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
