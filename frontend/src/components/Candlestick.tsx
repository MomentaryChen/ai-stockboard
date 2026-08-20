/**
 * Recharts has no candlestick series, so we render one as a custom <Bar> shape.
 *
 * The trick: give the Bar a *range* dataKey of [low, high] so Recharts lays the
 * bar out across the candle's full vertical extent -- wicks included, which also
 * makes the Y axis domain correct. Inside the shape, `y .. y + height` therefore
 * maps linearly onto `high .. low`, so the open/close body is a simple
 * interpolation. No access to the axis scale needed.
 */

export interface CandleDatum {
  date: string
  open: number
  high: number
  low: number
  close: number
}

interface CandleShapeProps {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: CandleDatum
  upColor?: string
  downColor?: string
}

export function CandleShape({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
  payload,
  upColor = '#f0464a',
  downColor = '#22b573',
}: CandleShapeProps) {
  if (!payload) return null

  const { open, high, low, close } = payload
  if ([open, high, low, close].some((v) => v === null || v === undefined)) return null

  const isUp = close >= open
  const color = isUp ? upColor : downColor
  const centerX = x + width / 2
  const bodyWidth = Math.max(width * 0.62, 1)
  const bodyX = centerX - bodyWidth / 2

  // Flat candle (high === low): a single horizontal tick is all there is to draw.
  const span = high - low
  if (span <= 0 || height <= 0) {
    return (
      <line
        x1={bodyX}
        x2={bodyX + bodyWidth}
        y1={y}
        y2={y}
        stroke={color}
        strokeWidth={1.5}
      />
    )
  }

  const priceToY = (price: number) => y + ((high - price) / span) * height
  const yOpen = priceToY(open)
  const yClose = priceToY(close)
  const bodyTop = Math.min(yOpen, yClose)
  // Keep doji bodies visible instead of collapsing to nothing.
  const bodyHeight = Math.max(Math.abs(yClose - yOpen), 1)

  return (
    <g>
      {/* upper + lower wick */}
      <line x1={centerX} x2={centerX} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      {/* real body: solid red for up days, solid green for down days */}
      <rect
        x={bodyX}
        y={bodyTop}
        width={bodyWidth}
        height={bodyHeight}
        fill={color}
        stroke={color}
        strokeWidth={1}
      />
    </g>
  )
}
