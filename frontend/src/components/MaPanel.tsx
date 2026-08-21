import type { MovingAverages } from '../api/types'
import { useI18n, type MessageKey } from '../i18n'
import { direction, fmtPrice, fmtSigned } from '../utils/format'

/** MA5/MA10 read the same in both languages; only the two that name a Taiwan
 *  trading period (月線/季線) carry a label. */
const WINDOWS: Array<{ key: keyof MovingAverages; label?: MessageKey }> = [
  { key: 'ma5' },
  { key: 'ma10' },
  { key: 'ma20', label: 'ma.ma20' },
  { key: 'ma60', label: 'ma.ma60' },
]

/** Latest moving averages, each compared against the latest close. */
export default function MaPanel({
  mas,
  latestClose,
}: {
  mas: MovingAverages
  latestClose: number | null
}) {
  const { t } = useI18n()

  return (
    <section className="card">
      <h2 className="card-title">{t('ma.title')}</h2>
      <table className="data">
        <thead>
          <tr>
            <th>{t('ma.period')}</th>
            <th>{t('ma.average')}</th>
            <th>{t('ma.bias')}</th>
          </tr>
        </thead>
        <tbody>
          {WINDOWS.map(({ key, label }) => {
            const value = mas[key]
            const diff =
              value !== null && latestClose !== null ? latestClose - value : null
            return (
              <tr key={key}>
                <td>{label ? t(label) : key.toUpperCase()}</td>
                <td>{fmtPrice(value)}</td>
                <td className={diff === null ? '' : direction(diff)}>
                  {diff === null ? '--' : fmtSigned(diff)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="dim" style={{ margin: '10px 0 0' }}>
        {t('ma.note')}
      </p>
    </section>
  )
}
