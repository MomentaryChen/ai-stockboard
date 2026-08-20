import type { DividendResponse } from '../api/types'
import { translateDividendKind, useI18n } from '../i18n'
import { fmtPrice } from '../utils/format'

/** Recent ex-right / ex-dividend days, plus trailing cash yield when we have it. */
export default function DividendCard({ data }: { data: DividendResponse }) {
  const { t } = useI18n()

  const yieldText =
    data.yield_percent === null ? '--' : `${fmtPrice(data.yield_percent)}%`

  return (
    <section className="card">
      <h2 className="card-title">{t('dividend.title')}</h2>

      <div className="stat-grid" style={{ marginBottom: 12 }}>
        <div>
          <div className="stat-label">{t('dividend.ttmCash')}</div>
          <div className="stat-value">{fmtPrice(data.ttm_cash, 4)}</div>
        </div>
        <div>
          <div className="stat-label">{t('dividend.yield')}</div>
          <div className="stat-value">{yieldText}</div>
        </div>
      </div>

      {data.events.length === 0 ? (
        <p className="dim" style={{ margin: 0 }}>
          {data.coverage === 'recent'
            ? t('dividend.emptyRecent')
            : t('dividend.emptyHistory')}
        </p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>{t('dividend.colExDate')}</th>
              <th>{t('dividend.colKind')}</th>
              <th>{t('dividend.colCash')}</th>
            </tr>
          </thead>
          <tbody>
            {data.events.map((event) => (
              <tr key={event.ex_date}>
                <td>
                  {event.ex_date}
                  {event.upcoming ? ` ${t('dividend.upcoming')}` : ''}
                </td>
                <td>{translateDividendKind(event.kind, t)}</td>
                <td>{fmtPrice(event.cash_dividend, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {data.coverage === 'recent'
          ? t('dividend.noteRecent')
          : t('dividend.noteHistory')}
      </p>
    </section>
  )
}
