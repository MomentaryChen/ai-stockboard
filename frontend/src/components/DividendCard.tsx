import type { DividendResponse } from '../api/types'
import { fmtPrice } from '../utils/format'

const KIND_LABEL: Record<string, string> = {
  息: '除息',
  權: '除權',
  權息: '除權息',
}

/** Recent ex-right / ex-dividend days, plus trailing cash yield when we have it. */
export default function DividendCard({ data }: { data: DividendResponse }) {
  const yieldText =
    data.yield_percent === null ? '--' : `${fmtPrice(data.yield_percent)}%`

  return (
    <section className="card">
      <h2 className="card-title">股息</h2>

      <div className="stat-grid" style={{ marginBottom: 12 }}>
        <div>
          <div className="stat-label">近一年現金股利</div>
          <div className="stat-value">{fmtPrice(data.ttm_cash, 4)}</div>
        </div>
        <div>
          <div className="stat-label">殖利率</div>
          <div className="stat-value">{yieldText}</div>
        </div>
      </div>

      {data.events.length === 0 ? (
        <p className="dim" style={{ margin: 0 }}>
          {data.coverage === 'recent'
            ? '近期與預告表沒有這檔的除權息。'
            : '查詢期間沒有除權息紀錄。'}
        </p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>除息日</th>
              <th>種類</th>
              <th>現金股利</th>
            </tr>
          </thead>
          <tbody>
            {data.events.map((event) => (
              <tr key={event.ex_date}>
                <td>
                  {event.ex_date}
                  {event.upcoming ? ' 預告' : ''}
                </td>
                <td>{KIND_LABEL[event.kind] ?? event.kind}</td>
                <td>{fmtPrice(event.cash_dividend, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {data.coverage === 'recent'
          ? '上櫃只公開近期計算結果與除權息預告，沒有上市那種年度歷史表。'
          : '殖利率 = 近一年現金股利 ÷ 最新收盤。除息列的現金股利取自權值+息值。'}
      </p>
    </section>
  )
}
