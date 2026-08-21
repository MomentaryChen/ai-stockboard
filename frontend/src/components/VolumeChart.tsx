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
import { useI18n } from '../i18n'
import { fmtInt, fmtLotsAxis } from '../utils/format'

const UP = '#f0464a'
const DOWN = '#22b573'

function VolumeTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: ReadonlyArray<{ payload: ChartRow }>
}) {
  const { t } = useI18n()

  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="chart-tooltip">
      <div className="t-date">{row.date}</div>
      <div className="t-row">
        <span className="muted">{t('chart.volume')}</span>
        <span>{t('chart.lots', { value: fmtInt(row.volumeLots) })}</span>
      </div>
    </div>
  )
}

/** Volume bars, coloured by that day's direction and hover-linked to PriceChart. */
export default function VolumeChart({ rows }: { rows: ChartRow[] }) {
  const { locale, t } = useI18n()

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
          tickFormatter={(v: number) => fmtLotsAxis(v, locale)}
        />
        <Tooltip content={<VolumeTooltip />} cursor={{ fill: 'rgba(76,141,255,0.08)' }} />
        <Bar dataKey="volumeLots" name={t('chart.volumeLots')} isAnimationActive={false}>
          {rows.map((row) => (
            <Cell key={row.date} fill={row.close >= row.open ? UP : DOWN} fillOpacity={0.7} />
          ))}
        </Bar>
      </ComposedChart>
    </ResponsiveContainer>
  )
}
