import { Link } from 'react-router-dom'

import { useI18n } from '../i18n'
import { direction, fmtLots, fmtPrice, fmtSigned } from '../utils/format'
import { patternKey, type OpenView } from '../utils/openIntel'

interface Props {
  /** One row per watchlist entry that had data, in watchlist order. */
  views: OpenView[]
  /** Watchlist entries nothing could answer for, with the reason. */
  missing: Array<{ sid: string; reason: string }>
}

/**
 * The watchlist, seen from the open rather than from the last price.
 *
 * Sorted by nothing: watchlist order is the order the user put them in, and
 * re-ranking by gap every ten seconds would make the row you were reading move
 * out from under the cursor mid-session.
 */
export default function OpenWatchlistTable({ views, missing }: Props) {
  const { t } = useI18n()

  return (
    <table className="data">
      <thead>
        <tr>
          <th>{t('open.colStock')}</th>
          <th>{t('stat.open')}</th>
          <th>{t('open.gap')}</th>
          <th>{t('open.colLast')}</th>
          <th>{t('open.fromOpen')}</th>
          <th>{t('stat.volumeLots')}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {views.map((view) => (
          <tr key={view.sid}>
            <td>
              <Link to={`/stock/${view.sid}`} className="open-row-link">
                <span className="tabular">{view.sid}</span> {view.name}
              </Link>
            </td>
            <td>{fmtPrice(view.open)}</td>
            <td className={direction(view.gap)}>
              {fmtSigned(view.gap)}
              {view.gapPct !== null && ` (${fmtSigned(view.gapPct)}%)`}
            </td>
            <td className={direction(view.change)}>
              {fmtPrice(view.last)}
              {view.changePct !== null && (
                <span className="dim"> {fmtSigned(view.changePct)}%</span>
              )}
            </td>
            <td className={direction(view.fromOpen)}>
              {fmtSigned(view.fromOpen)}
              {view.fromOpenPct !== null && ` (${fmtSigned(view.fromOpenPct)}%)`}
            </td>
            <td>{fmtLots(view.capacity)}</td>
            <td>
              <span className="dim">{t(patternKey(view))}</span>
            </td>
          </tr>
        ))}

        {/* A watchlist entry with no data still has to appear: silently dropping
            it reads as "this stock did not move", not as "we have nothing". */}
        {missing.map((entry) => (
          <tr key={entry.sid}>
            <td>
              <Link to={`/stock/${entry.sid}`} className="open-row-link">
                <span className="tabular">{entry.sid}</span>
              </Link>
            </td>
            <td colSpan={6} className="dim">
              {entry.reason}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
