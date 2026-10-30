import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { EngineValidationItem } from '../api/types'
import EngineAdvice from '../components/EngineAdvice'
import EngineTrackRecord from '../components/EngineTrackRecord'
import { useI18n } from '../i18n'
import { ENGINE_SECTOR_ORDER, translateEngineSector } from '../i18n/serverText'
import { errorMessage } from '../utils/errors'

function signed(share: number): string {
  return `${share >= 0 ? '+' : ''}${(share * 100).toFixed(1)}%`
}

/**
 * Says up front when the holder's grades have not earned trust.
 *
 * Driven by the stored check so it cannot outlive the finding: if a later engine
 * version's weak names really do trail its strong ones, the warning goes away by
 * itself rather than staying on the page as a stale claim.
 */
function ValidationNote({ rows }: { rows: EngineValidationItem[] }) {
  const { t } = useI18n()
  const weak = rows.find((row) => row.state === 'weak')
  const strong = rows.find((row) => row.state === 'strong')
  if (!weak || !strong) return null
  const values = { weak: signed(weak.excess_60d), strong: signed(strong.excess_60d) }
  return (
    <p className="engine-warning">
      {t(weak.excess_60d >= strong.excess_60d ? 'engine.noSellSignal' : 'engine.sellSignalChecked', values)}
    </p>
  )
}

export default function EnginePicks() {
  const { t } = useI18n()
  // In the URL so a sector can be bookmarked and survives the back button from
  // a stock page.
  const [params, setParams] = useSearchParams()

  const overview = useQuery({ queryKey: ['engine', 'overview'], queryFn: api.getEngineOverview })

  const order = (sector: string) => {
    const index = ENGINE_SECTOR_ORDER.indexOf(sector)
    return index === -1 ? ENGINE_SECTOR_ORDER.length : index
  }
  const sectors = [...(overview.data?.sectors ?? [])].sort((a, b) => order(a.sector) - order(b.sector))
  const showTrack = params.get('view') === 'track'
  const selected = showTrack ? null : (params.get('sector') ?? sectors[0]?.sector ?? null)

  const stocks = useQuery({
    queryKey: ['engine', 'recommendations', selected, overview.data?.as_of],
    queryFn: () => api.getEngineRecommendations(selected as string),
    enabled: selected !== null,
  })

  if (overview.isLoading) {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }
  if (overview.error) {
    return <div className="center-note">{errorMessage(overview.error, t)}</div>
  }
  if (sectors.length === 0) {
    return <div className="center-note">{t('engine.empty')}</div>
  }

  return (
    <div className="engine-layout">
      <nav className="engine-side" aria-label={t('engine.sectors')}>
        <div className="dim engine-side-title">{t('engine.sectors')}</div>
        {sectors.map((sector) => (
          <button
            type="button"
            key={sector.sector}
            className={`engine-side-item ${sector.sector === selected ? 'active' : ''}`}
            aria-current={sector.sector === selected ? 'true' : undefined}
            onClick={() => setParams({ sector: sector.sector })}
          >
            {translateEngineSector(sector.sector, t)}
          </button>
        ))}
        <button
          type="button"
          className={`engine-side-item engine-side-track ${showTrack ? 'active' : ''}`}
          aria-current={showTrack ? 'true' : undefined}
          onClick={() => setParams({ view: 'track' })}
        >
          {t('engine.trackNav')}
        </button>
      </nav>

      {showTrack ? (
        <EngineTrackRecord />
      ) : (
      <div className="stack engine-main-column">
        <div>
          <h1 className="engine-title">{selected && translateEngineSector(selected, t)}</h1>
          <p className="dim">{t('engine.asOf', { date: overview.data?.as_of ?? '' })}</p>
          {overview.data?.stale && <p className="engine-warning">{t('engine.stale')}</p>}
        </div>

        <ValidationNote rows={overview.data?.hold_backtest ?? []} />

        <details className="engine-how">
          <summary>{t('engine.howTitle')}</summary>
          <p>{t('engine.howWindows')}</p>
          <p>{t('engine.howPrice')}</p>
          <p>{t('engine.howHold')}</p>
          <p>{t('engine.howLimits')}</p>
        </details>

        {stocks.isLoading ? (
          <div className="center-note">
            <span className="spinner" />
          </div>
        ) : stocks.error ? (
          <div className="center-note">{errorMessage(stocks.error, t)}</div>
        ) : (
          (stocks.data ?? []).map((item) => <EngineAdvice key={item.sid} item={item} linked />)
        )}

        <p className="dim">{t('engine.disclaimer')}</p>
      </div>
      )}
    </div>
  )
}
