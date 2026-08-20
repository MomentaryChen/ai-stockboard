import { useMemo } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { DailyPricePoint, MaSeriesPoint } from '../api/types'
import { CandleShape } from './Candlestick'
import { fmtLots, fmtPrice, shortDate } from '../utils/format'

const UP = '#f0464a'
const DOWN = '#22b573'
const MA_COLORS: Record<string, string> = {
  ma5: '#f5c26b',
  ma10: '#4c8dff',
  ma20: '#b98bff',
  ma60: '#5d6478',
}

export interface ChartRow {
  date: string
  label: string
  open: number
  high: number
  low: number
  close: number
  change: number | null
  capacity: number | null
  /** Volume in 張 (1 張 = 1000 股) so the chart axis matches how traders read it. */
  volumeLots: number | null
  hl: [number, number]
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
}

/** Join price rows with the MA series the API computed, dropping non-trading days. */
export function buildChartRows(
  prices: DailyPricePoint[],
  maSeries: MaSeriesPoint[] | undefined,
): ChartRow[] {
  const maByDate = new Map((maSeries ?? []).map((m) => [m.date, m]))

  return prices
    .filter(
      (p) =>
        p.open !== null && p.high !== null && p.low !== null && p.close !== null,
    )
    .map((p) => {
      const ma = maByDate.get(p.date)
      return {
        date: p.date,
        label: shortDate(p.date),
        open: p.open as number,
        high: p.high as number,
        low: p.low as number,
        close: p.close as number,
        change: p.change,
        capacity: p.capacity,
        volumeLots: p.capacity === null ? null : Math.round(p.capacity / 1000),
        hl: [p.low as number, p.high as number] as [number, number],
        ma5: ma?.ma5 ?? null,
        ma10: ma?.ma10 ?? null,
        ma20: ma?.ma20 ?? null,
        ma60: ma?.ma60 ?? null,
      }
    })
}

function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as ChartRow
  const dir = row.close >= row.open ? 'up' : 'down'

  const lines: Array<[string, string, string?]> = [
    ['開', fmtPrice(row.open)],
    ['高', fmtPrice(row.high)],
    ['低', fmtPrice(row.low)],
    ['收', fmtPrice(row.close), dir],
    ['量(張)', fmtLots(row.capacity)],
  ]

  return (
    <div className="chart-tooltip">
      <div className="t-date">{row.date}</div>
      {lines.map(([label, value, cls]) => (
        <div className="t-row" key={label}>
          <span className="muted">{label}</span>
          <span className={cls ?? ''}>{value}</span>
        </div>
      ))}
      {(['ma5', 'ma10', 'ma20', 'ma60'] as const).map((key) =>
        row[key] === null ? null : (
          <div className="t-row" key={key}>
            <span style={{ color: MA_COLORS[key] }}>{key.toUpperCase()}</span>
            <span>{fmtPrice(row[key])}</span>
          </div>
        ),
      )}
    </div>
  )
}

interface Props {
  rows: ChartRow[]
  mode: 'candle' | 'line'
  visibleMas: string[]
}

export default function PriceChart({ rows, mode, visibleMas }: Props) {
  // Pad the domain so candles never touch the plot edges, then snap the bounds
  // to a round step so the axis reads 2150 / 2300 / 2450 rather than 2151.6.
  const domain = useMemo<[number, number]>(() => {
    if (rows.length === 0) return [0, 1]
    const min = Math.min(...rows.map((r) => r.low))
    const max = Math.max(...rows.map((r) => r.high))
    const span = max - min || max * 0.02
    const pad = span * 0.08
    const step = Math.pow(10, Math.floor(Math.log10(span || 1))) / 2
    return [
      Math.max(0, Math.floor((min - pad) / step) * step),
      Math.ceil((max + pad) / step) * step,
    ]
  }, [rows])

  return (
    <ResponsiveContainer width="100%" height={360}>
      <ComposedChart data={rows} syncId="stock" margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#262d3d" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: '#262d3d' }} minTickGap={24} />
        <YAxis
          domain={domain}
          tickLine={false}
          axisLine={false}
          width={56}
          tickFormatter={(v: number) => v.toFixed(1)}
          orientation="right"
        />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#364155' }} />
        <Legend
          wrapperStyle={{ fontSize: 12, color: '#8b93a7' }}
          formatter={(value: string) => <span style={{ color: '#8b93a7' }}>{value}</span>}
        />

        {mode === 'candle' ? (
          <Bar
            dataKey="hl"
            name="K線"
            isAnimationActive={false}
            shape={<CandleShape upColor={UP} downColor={DOWN} />}
            legendType="none"
          />
        ) : (
          <Line
            type="monotone"
            dataKey="close"
            name="收盤價"
            stroke={UP}
            strokeWidth={1.8}
            dot={false}
            isAnimationActive={false}
          />
        )}

        {(['ma5', 'ma10', 'ma20', 'ma60'] as const)
          .filter((key) => visibleMas.includes(key))
          .map((key) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              name={key.toUpperCase()}
              stroke={MA_COLORS[key]}
              strokeWidth={1.4}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
