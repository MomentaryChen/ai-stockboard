import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import BestFourPointCard from '../components/BestFourPointCard'
import MaPanel from '../components/MaPanel'
import PriceChart, { buildChartRows } from '../components/PriceChart'
import StockSearch from '../components/StockSearch'
import VolumeChart from '../components/VolumeChart'
import { direction, fmtCompact, fmtLots, fmtPrice, fmtSigned } from '../utils/format'

const RANGES = [
  { label: '1個月', months: 1 },
  { label: '3個月', months: 3 },
  { label: '6個月', months: 6 },
  { label: '1年', months: 12 },
]

const MA_OPTIONS = ['ma5', 'ma10', 'ma20', 'ma60']

export default function StockDetail() {
  const { sid = '2330' } = useParams()
  const navigate = useNavigate()

  const [months, setMonths] = useState(3)
  const [mode, setMode] = useState<'candle' | 'line'>('candle')
  const [visibleMas, setVisibleMas] = useState<string[]>(['ma5', 'ma20'])

  const history = useQuery({
    queryKey: ['history', sid, months],
    queryFn: () => api.getHistory(sid, months),
  })

  const analysis = useQuery({
    queryKey: ['analysis', 'traditional', sid, months],
    queryFn: () => api.getTraditionalAnalysis(sid, months),
  })

  const rows = useMemo(
    () => buildChartRows(history.data?.data ?? [], analysis.data?.ma_series),
    [history.data, analysis.data],
  )

  const latest = rows.at(-1)
  const previous = rows.at(-2)
  const changePct =
    latest && previous && previous.close
      ? ((latest.close - previous.close) / previous.close) * 100
      : null
  const dir = direction(latest?.change ?? null)

  function toggleMa(key: string) {
    setVisibleMas((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    )
  }

  const error = history.error ?? analysis.error

  return (
    <div className="stack">
      <div className="row wrap" style={{ gap: 16 }}>
        <StockSearch onSelect={(stock) => navigate(`/stock/${stock.code}`)} />
      </div>

      {error && (
        <div className="banner banner-error">
          載入失敗：{(error as Error).message}
          <br />
          <span className="dim">
            若訊息與資料庫有關，請先在 deployment/ 目錄執行 docker compose up -d 啟動 PostgreSQL。
          </span>
        </div>
      )}

      <section className="card">
        <div className="row-between wrap">
          <div className="stock-head">
            <span className="sid">{sid}</span>
            <span className="sname">{history.data?.name ?? analysis.data?.name ?? ''}</span>
            {history.data && <span className="tag">{history.data.source.toUpperCase()}</span>}
          </div>

          <div className="row wrap" style={{ gap: 16 }}>
            <div>
              <span className={`price-now ${dir}`}>{fmtPrice(latest?.close ?? null)}</span>{' '}
              <span className={`price-change ${dir}`}>
                {fmtSigned(latest?.change ?? null)}
                {changePct !== null ? ` (${fmtSigned(changePct)}%)` : ''}
              </span>
            </div>
          </div>
        </div>

        <div className="stat-grid" style={{ marginTop: 16 }}>
          <div>
            <div className="stat-label">開盤</div>
            <div className="stat-value">{fmtPrice(latest?.open ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">最高</div>
            <div className="stat-value up">{fmtPrice(latest?.high ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">最低</div>
            <div className="stat-value down">{fmtPrice(latest?.low ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">成交量(張)</div>
            <div className="stat-value">{fmtLots(latest?.capacity ?? null)}</div>
          </div>
          <div>
            <div className="stat-label">交易日</div>
            <div className="stat-value">{rows.length}</div>
          </div>
          <div>
            <div className="stat-label">最新日期</div>
            <div className="stat-value">{latest?.date ?? '--'}</div>
          </div>
        </div>
      </section>

      <div className="grid-detail">
        <section className="card">
          <div className="row-between wrap" style={{ marginBottom: 12 }}>
            <div className="segmented">
              {RANGES.map((range) => (
                <button
                  key={range.months}
                  type="button"
                  className={`btn btn-sm ${months === range.months ? 'active' : ''}`}
                  onClick={() => setMonths(range.months)}
                >
                  {range.label}
                </button>
              ))}
            </div>

            <div className="row wrap" style={{ gap: 12 }}>
              <div className="segmented">
                <button
                  type="button"
                  className={`btn btn-sm ${mode === 'candle' ? 'active' : ''}`}
                  onClick={() => setMode('candle')}
                >
                  K線
                </button>
                <button
                  type="button"
                  className={`btn btn-sm ${mode === 'line' ? 'active' : ''}`}
                  onClick={() => setMode('line')}
                >
                  收盤線
                </button>
              </div>

              <div className="segmented">
                {MA_OPTIONS.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={`btn btn-sm ${visibleMas.includes(key) ? 'active' : ''}`}
                    onClick={() => toggleMa(key)}
                  >
                    {key.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {history.isLoading ? (
            <div className="center-note">
              <div className="row" style={{ justifyContent: 'center' }}>
                <span className="spinner" />
                <span style={{ marginLeft: 10 }}>
                  首次查詢需向 TWSE 逐月抓取，並受每 5 秒 3 次的速率限制，請稍候…
                </span>
              </div>
            </div>
          ) : rows.length === 0 ? (
            <div className="center-note">沒有資料</div>
          ) : (
            <>
              <PriceChart rows={rows} mode={mode} visibleMas={visibleMas} />
              <VolumeChart rows={rows} />
            </>
          )}

          {history.data && (
            <p className="dim" style={{ marginTop: 10 }}>
              本次向來源抓取 {history.data.fetched_months.length} 個月
              {history.data.fetched_months.length > 0 &&
                `（${history.data.fetched_months.join(', ')}）`}
              ，由 PostgreSQL 快取提供 {history.data.cached_months.length} 個月
            </p>
          )}
        </section>

        <div className="stack">
          {analysis.data && (
            <>
              {/* Labelled explicitly so AI-assisted analysis can sit beside it. */}
              <div className="section-label">傳統分析</div>
              <BestFourPointCard
                result={analysis.data.best_four_point}
                asOf={analysis.data.as_of}
                sampleSize={analysis.data.sample_size}
              />
              <MaPanel
                mas={analysis.data.moving_averages}
                latestClose={analysis.data.latest_close}
              />
            </>
          )}

          <section className="card">
            <h2 className="card-title">近 10 日</h2>
            <table className="data">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>收盤</th>
                  <th>漲跌</th>
                  <th>成交金額</th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .slice(-10)
                  .reverse()
                  .map((row) => {
                    const point = history.data?.data.find((d) => d.date === row.date)
                    return (
                      <tr key={row.date}>
                        <td>{row.date.slice(5)}</td>
                        <td>{fmtPrice(row.close)}</td>
                        <td className={direction(row.change)}>{fmtSigned(row.change)}</td>
                        <td>{fmtCompact(point?.turnover ?? null)}</td>
                      </tr>
                    )
                  })}
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  )
}
