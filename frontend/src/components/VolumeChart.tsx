import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ChartRow } from './PriceChart'
import { fmtInt } from '../utils/format'

const UP = '#f0464a'
const DOWN = '#22b573'

function VolumeTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as ChartRow
  return (
    <div className="chart-tooltip">
      <div className="t-date">{row.date}</div>
      <div className="t-row">
        <span className="muted">成交量</span>
        <span>{fmtInt(row.volumeLots)} 張</span>
      </div>
    </div>
  )
}

/** Volume bars, coloured by that day's direction and hover-linked to PriceChart. */
export default function VolumeChart({ rows }: { rows: ChartRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={130}>
      <ComposedChart data={rows} syncId="stock" margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#262d3d" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: '#262d3d' }} minTickGap={24} />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={56}
          orientation="right"
          tickFormatter={(v: number) => {
            // Keep a decimal below 10 萬 so a 25,000 張 tick does not read as "3 萬".
            if (v >= 100_000) return `${Math.round(v / 10_000)} 萬張`
            if (v >= 10_000) return `${(v / 10_000).toFixed(1)} 萬張`
            return `${Math.round(v)} 張`
          }}
        />
        <Tooltip content={<VolumeTooltip />} cursor={{ fill: 'rgba(76,141,255,0.08)' }} />
        <Bar dataKey="volumeLots" name="成交量(張)" isAnimationActive={false}>
          {rows.map((row) => (
            <Cell key={row.date} fill={row.close >= row.open ? UP : DOWN} fillOpacity={0.7} />
          ))}
        </Bar>
      </ComposedChart>
    </ResponsiveContainer>
  )
}
