import { useI18n } from '../i18n'
import { direction, fmtCompact, fmtSigned } from '../utils/format'
import { patternKey, type OpenView } from '../utils/openIntel'

interface Props {
  view: OpenView
  /** Index levels read as 44,933.74 and stock prices as 512.00. */
  format: (value: number | null | undefined) => string
  /** Turnover the view has no figure for -- a live quote does not carry it. */
  turnoverFallback?: number | null
  locale?: 'zh-TW' | 'en'
}

/**
 * The three numbers the opening view is actually about, as one row:
 *
 *   昨收 -> 開盤     the gap. Priced overnight, before this session traded.
 *   開盤 -> 現價     what the session itself has done with that start.
 *   the verdict      the two directions combined -- 開高走低 and its siblings.
 *
 * Deliberately separate from the day's high/low/turnover tiles, which say where
 * the price has *been*. This says where it started and which way it went from
 * there, and those are the questions the first hour of a session is about.
 */
export default function OpenIntelStrip({ view, format, turnoverFallback, locale }: Props) {
  const { t } = useI18n()

  const gapDir = direction(view.gap)
  const driftDir = direction(view.fromOpen)
  // The verdict chip borrows the signal palette: red = up on a Taiwan board.
  const verdictTone =
    view.gapDirection === 'flat' && view.driftDirection === 'flat'
      ? 'hold'
      : view.driftDirection === 'down'
        ? 'sell'
        : view.driftDirection === 'up'
          ? 'buy'
          : 'hold'

  const turnover = view.turnover ?? turnoverFallback ?? null

  return (
    <div className="open-strip">
      <div className={`signal signal-sm signal-${verdictTone}`}>{t(patternKey(view))}</div>

      <div className="open-legs">
        <div className="open-leg">
          <div className="stat-label">{t('open.gap')}</div>
          <div className={`stat-value ${gapDir}`}>
            {fmtSigned(view.gap)}
            {view.gapPct !== null && ` (${fmtSigned(view.gapPct)}%)`}
          </div>
          <div className="dim">
            {t('quote.prevClose')} {format(view.prevClose)} → {format(view.open)}
          </div>
        </div>

        <div className="open-leg">
          <div className="stat-label">{t('open.fromOpen')}</div>
          <div className={`stat-value ${driftDir}`}>
            {fmtSigned(view.fromOpen)}
            {view.fromOpenPct !== null && ` (${fmtSigned(view.fromOpenPct)}%)`}
          </div>
          <div className="dim">
            {t('stat.open')} {format(view.open)} → {format(view.last)}
          </div>
        </div>

        <div className="open-leg">
          <div className="stat-label">{t('stat.turnover')}</div>
          <div className="stat-value">{fmtCompact(turnover, locale)}</div>
          <div className="dim">
            {t('stat.high')} {format(view.high)} · {t('stat.low')} {format(view.low)}
          </div>
        </div>
      </div>
    </div>
  )
}
