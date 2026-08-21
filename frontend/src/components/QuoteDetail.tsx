/**
 * Everything about a quote that is not the headline price.
 *
 * The same block serves the card and the expanded board row, which is what
 * keeps the compact board from being a second, drifting implementation of the
 * quote view: collapsing a row hides this markup, it does not replace it with
 * a shorter version of the same numbers.
 */

import type { RealtimeQuote } from '../api/types'
import { useI18n } from '../i18n'
import { fmtInt, fmtPrice } from '../utils/format'
import QuoteDepth from './QuoteDepth'

export default function QuoteDetail({ quote }: { quote: RealtimeQuote }) {
  const { t } = useI18n()

  return (
    <>
      <div className="stat-grid">
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
        <QuoteDepth
          title={t('quote.bidDepth')}
          prices={quote.best_bid_price}
          volumes={quote.best_bid_volume}
          className="up"
        />
        <QuoteDepth
          title={t('quote.askDepth')}
          prices={quote.best_ask_price}
          volumes={quote.best_ask_volume}
          className="down"
        />
      </div>

      <p className="dim" style={{ margin: '12px 0 0' }}>
        {t('quote.quotedAt', { time: quote.time })}
      </p>
    </>
  )
}
