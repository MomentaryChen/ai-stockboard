import type { MovingAverages } from '../api/types'
import { direction, fmtPrice, fmtSigned } from '../utils/format'

const WINDOWS: Array<{ key: keyof MovingAverages; label: string }> = [
  { key: 'ma5', label: 'MA5' },
  { key: 'ma10', label: 'MA10' },
  { key: 'ma20', label: 'MA20 (月線)' },
  { key: 'ma60', label: 'MA60 (季線)' },
]

/** Latest moving averages, each compared against the latest close. */
export default function MaPanel({
  mas,
  latestClose,
}: {
  mas: MovingAverages
  latestClose: number | null
}) {
  return (
    <section className="card">
      <h2 className="card-title">均線</h2>
      <table className="data">
        <thead>
          <tr>
            <th>期間</th>
            <th>均價</th>
            <th>乖離</th>
          </tr>
        </thead>
        <tbody>
          {WINDOWS.map(({ key, label }) => {
            const value = mas[key]
            const diff =
              value !== null && latestClose !== null ? latestClose - value : null
            return (
              <tr key={key}>
                <td>{label}</td>
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
        乖離 = 最新收盤價 − 均價
      </p>
    </section>
  )
}
