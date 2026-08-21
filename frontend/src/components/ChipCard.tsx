import type { ChipFlow, ChipResponse } from '../api/types'
import { useI18n } from '../i18n'
import { direction, fmtInt, fmtSignedInt, fmtSignedLots } from '../utils/format'

function streakText(flow: ChipFlow, t: ReturnType<typeof useI18n>['t']): string {
  if (flow.streak === 'buy') return t('chip.streakBuy', { days: flow.streak_days })
  if (flow.streak === 'sell') return t('chip.streakSell', { days: flow.streak_days })
  return t('chip.streakNone')
}

function streakClass(flow: ChipFlow): string {
  if (flow.streak === 'buy') return 'up'
  if (flow.streak === 'sell') return 'down'
  return 'flat'
}

/** Institutional net buying and margin balances. Not a Buy/Sell signal. */
export default function ChipCard({ data }: { data: ChipResponse }) {
  const { t } = useI18n()

  if (data.coverage === 'none') return null

  const asOf = data.as_of ?? ''

  return (
    <section className="card">
      <h2 className="card-title">{t('chip.title')}</h2>
      {asOf ? (
        <p className="dim" style={{ margin: '0 0 12px' }}>
          {t('chip.asOf', { date: asOf })}
        </p>
      ) : null}

      <div className="stat-grid" style={{ marginBottom: 12 }}>
        <div>
          <div className="stat-label">{t('chip.foreign')}</div>
          <div className={`stat-value ${streakClass(data.foreign)}`}>
            {streakText(data.foreign, t)}
          </div>
          <div className={`dim ${direction(data.foreign.net_5d)}`}>
            {t('chip.net5d', { value: fmtSignedLots(data.foreign.net_5d) })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('chip.trust')}</div>
          <div className={`stat-value ${streakClass(data.trust)}`}>
            {streakText(data.trust, t)}
          </div>
          <div className={`dim ${direction(data.trust.net_5d)}`}>
            {t('chip.net5d', { value: fmtSignedLots(data.trust.net_5d) })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('chip.total')}</div>
          <div className={`stat-value ${streakClass(data.total)}`}>
            {streakText(data.total, t)}
          </div>
          <div className={`dim ${direction(data.total.net_5d)}`}>
            {t('chip.net5d', { value: fmtSignedLots(data.total.net_5d) })}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('chip.margin')}</div>
          <div className="stat-value">{fmtInt(data.margin_balance)}</div>
          <div className={`dim ${direction(data.margin_change)}`}>
            {fmtSignedInt(data.margin_change)} {t('chip.lots')}
          </div>
        </div>
        <div>
          <div className="stat-label">{t('chip.short')}</div>
          <div className="stat-value">{fmtInt(data.short_balance)}</div>
          <div className={`dim ${direction(data.short_change)}`}>
            {fmtSignedInt(data.short_change)} {t('chip.lots')}
          </div>
        </div>
      </div>

      {data.rows.length === 0 ? (
        <p className="dim" style={{ margin: 0 }}>
          {t('chip.empty')}
        </p>
      ) : (
        <table className="data chip-table">
          <thead>
            <tr>
              <th>{t('chip.colDate')}</th>
              <th>{t('chip.foreign')}</th>
              <th>{t('chip.trust')}</th>
              <th>{t('chip.dealer')}</th>
              <th>{t('chip.total')}</th>
              <th>{t('chip.margin')}</th>
              <th>{t('chip.short')}</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.date}>
                <td>{row.date.slice(5)}</td>
                <td className={direction(row.foreign_net)}>
                  {fmtSignedLots(row.foreign_net)}
                </td>
                <td className={direction(row.trust_net)}>
                  {fmtSignedLots(row.trust_net)}
                </td>
                <td className={direction(row.dealer_net)}>
                  {fmtSignedLots(row.dealer_net)}
                </td>
                <td className={direction(row.total_net)}>
                  {fmtSignedLots(row.total_net)}
                </td>
                <td>
                  {fmtInt(row.margin_balance)}
                  <span className={`dim ${direction(row.margin_change)}`}>
                    {' '}
                    {fmtSignedInt(row.margin_change)}
                  </span>
                </td>
                <td>
                  {fmtInt(row.short_balance)}
                  <span className={`dim ${direction(row.short_change)}`}>
                    {' '}
                    {fmtSignedInt(row.short_change)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {t('chip.note')}
      </p>
    </section>
  )
}
