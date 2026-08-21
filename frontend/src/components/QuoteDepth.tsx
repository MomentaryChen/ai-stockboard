/** 五檔 for one side of the book. Shared by the card and the expanded row. */

import { fmtInt, fmtPrice } from '../utils/format'

interface Props {
  title: string
  prices: number[]
  volumes: number[]
  /** 'up' for the bid side, 'down' for the ask side -- Taiwan colouring. */
  className: string
}

export default function QuoteDepth({ title, prices, volumes, className }: Props) {
  return (
    <div className="depth-col">
      <div className="depth-head">{title}</div>
      {prices.length === 0 && <div className="dim">--</div>}
      {prices.map((price, i) => (
        <div className="depth-row" key={`${price}-${i}`}>
          <span className={className}>{fmtPrice(price)}</span>
          <span className="muted">{fmtInt(volumes[i] ?? null)}</span>
        </div>
      ))}
    </div>
  )
}
