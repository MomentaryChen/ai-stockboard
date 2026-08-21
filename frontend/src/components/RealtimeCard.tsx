import { Link } from 'react-router-dom'

import type { BestFourPointResult, RealtimeQuote } from '../api/types'
import { translateBfpLabel, translateBfpReason, useI18n } from '../i18n'
import { direction, fmtInt, fmtPrice, fmtSigned } from '../utils/format'

interface Props {
  quote: RealtimeQuote
  onRemove?: (code: string) => void
  bfp?: BestFourPointResult
  bfpLoading?: boolean
}

function Depth({
  title,
  prices,
  volumes,
  className,
}: {
  title: string
  prices: number[]
  volumes: number[]
  className: string
}) {
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

/** Compact 四大買賣點 chip for a watchlist card. */
export function BfpChip({
  result,
  loading,
}: {
  result?: BestFourPointResult
  loading?: boolean
}) {
  const { t } = useI18n()

  if (loading && !result) {
    return (
      <div className="quote-bfp">
        <span className="dim" style={{ fontSize: 12 }}>
          {t('bfp.loading')}
        </span>
      </div>
    )
  }
  if (!result) return null

  return (
    <div className="quote-bfp">
      <div className={`signal signal-sm signal-${result.signal}`}>
        {translateBfpLabel(result.label, t)}
      </div>
      {result.reasons.length > 0 && (
        <ul className="quote-bfp-reasons">
          {result.reasons.map((reason) => (
            <li key={reason}>{translateBfpReason(reason, t)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function RealtimeCard({ quote, onRemove, bfp, bfpLoading }: Props) {
  const { t } = useI18n()
  const dir = direction(quote.change)

  return (
    <article className="card quote-card">
      {onRemove && (
        <button
          type="button"
          className="btn-icon remove"
          title={t('realtime.remove')}
          onClick={() => onRemove(quote.code)}
        >
          ×
        </button>
      )}

      <div className="row" style={{ gap: 10 }}>
        <Link
          to={`/stock/${quote.code}`}
          style={{ color: 'inherit', textDecoration: 'none', fontWeight: 700, fontSize: 17 }}
        >
          {quote.code} {quote.name}
        </Link>
      </div>

      <div className="row wrap" style={{ gap: 12, marginTop: 8 }}>
        <span className={`price-now ${dir}`}>{fmtPrice(quote.latest_trade_price)}</span>
        <span className={`price-change ${dir}`}>
          {fmtSigned(quote.change)}
          {quote.change_percent !== null ? ` (${fmtSigned(quote.change_percent)}%)` : ''}
        </span>
      </div>

      <BfpChip result={bfp} loading={bfpLoading} />

      <div className="stat-grid" style={{ marginTop: 14 }}>
        <div>
          <div className="stat-label">{t('stat.open')}</div>
          <div className="stat-value">{fmtPrice(quote.open)}</div>
        </div>
        <div>
          <div className="stat-label">{t('stat.high')}</div>
          <div className="stat-value up">{fmtPrice(quote.high)}</div>
        </div>
        <div>
          <div className="stat-label">{t('stat.low')}</div>
          <div className="stat-value down">{fmtPrice(quote.low)}</div>
        </div>
        <div>
          <div className="stat-label">{t('quote.prevClose')}</div>
          <div className="stat-value">{fmtPrice(quote.yesterday_close)}</div>
        </div>
        <div>
          <div className="stat-label">{t('quote.totalVolume')}</div>
          <div className="stat-value">{fmtInt(quote.accumulate_trade_volume)}</div>
        </div>
        <div>
          <div className="stat-label">{t('quote.tradeVolume')}</div>
          <div className="stat-value">{fmtInt(quote.trade_volume)}</div>
        </div>
      </div>

      <div className="depth">
        <Depth
          title={t('quote.bidDepth')}
          prices={quote.best_bid_price}
          volumes={quote.best_bid_volume}
          className="up"
        />
        <Depth
          title={t('quote.askDepth')}
          prices={quote.best_ask_price}
          volumes={quote.best_ask_volume}
          className="down"
        />
      </div>

      <p className="dim" style={{ margin: '12px 0 0' }}>
        {t('quote.quotedAt', { time: quote.time })}
      </p>
    </article>
  )
}
