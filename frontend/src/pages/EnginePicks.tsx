import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { EngineOverview, EngineRegion, EngineValidationItem } from '../api/types'
import EngineAdvice from '../components/EngineAdvice'
import EngineTrackRecord from '../components/EngineTrackRecord'
import { useI18n, type MessageKey } from '../i18n'
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

  const taiwan = useQuery({ queryKey: ['engine', 'overview', 'tw'], queryFn: () => api.getEngineOverview('tw') })
  const us = useQuery({ queryKey: ['engine', 'overview', 'us'], queryFn: () => api.getEngineOverview('us') })

  const order = (sector: string) => {
    const index = ENGINE_SECTOR_ORDER.indexOf(sector)
    return index === -1 ? ENGINE_SECTOR_ORDER.length : index
  }
  const sorted = (data?: EngineOverview) =>
    [...(data?.sectors ?? [])].sort((a, b) => order(a.sector) - order(b.sector))
  const groups = { tw: sorted(taiwan.data), us: sorted(us.data) }

  const region: EngineRegion =
    params.get('region') === 'us' || (groups.tw.length === 0 && groups.us.length > 0) ? 'us' : 'tw'
  const overview = region === 'us' ? us.data : taiwan.data
  const showTrack = params.get('view') === 'track'
  const selected = showTrack ? null : (params.get('sector') ?? groups[region][0]?.sector ?? null)

  const stocks = useQuery({
    queryKey: ['engine', 'recommendations', region, selected, overview?.as_of],
    queryFn: () => api.getEngineRecommendations(selected as string, region),
    enabled: selected !== null,
  })

  if (taiwan.isLoading || us.isLoading) {
    return (
      <div className="center-note">
        <span className="spinner" />
      </div>
    )
  }
  if (taiwan.error || us.error) {
    return <div className="center-note">{errorMessage(taiwan.error ?? us.error, t)}</div>
  }
  if (groups.tw.length === 0 && groups.us.length === 0) {
    return <div className="center-note">{t('engine.empty')}</div>
  }

  const sideGroup = (name: EngineRegion, title: MessageKey, track: MessageKey) =>
    groups[name].length > 0 && (
      <>
        <div className="dim engine-side-title">{t(title)}</div>
        {groups[name].map((sector) => {
          const active = region === name && sector.sector === selected
          return (
            <button
              type="button"
              key={sector.sector}
              className={`engine-side-item ${active ? 'active' : ''}`}
              aria-current={active ? 'true' : undefined}
              onClick={() => setParams({ region: name, sector: sector.sector })}
            >
              {translateEngineSector(sector.sector, t)}
            </button>
          )
        })}
        <button
          type="button"
          className={`engine-side-item engine-side-track ${showTrack && region === name ? 'active' : ''}`}
          aria-current={showTrack && region === name ? 'true' : undefined}
          onClick={() => setParams({ region: name, view: 'track' })}
        >
          {t(track)}
        </button>
      </>
    )

  return (
    <div className="engine-layout">
      <nav className="engine-side" aria-label={t('engine.sectors')}>
        {sideGroup('tw', 'engine.twSectors', 'engine.trackNav')}
        {sideGroup('us', 'engine.usSectors', 'engine.usTrackNav')}
      </nav>

      {showTrack ? (
        <EngineTrackRecord region={region} />
      ) : (
        <div className="stack engine-main-column">
          <div>
            <h1 className="engine-title">{selected && translateEngineSector(selected, t)}</h1>
            <p className="dim">{t('engine.asOf', { date: overview?.as_of ?? '' })}</p>
            {overview?.stale && <p className="engine-warning">{t('engine.stale')}</p>}
          </div>

          {region === 'us' && <p className="engine-warning">{t('engine.usNote')}</p>}
          <ValidationNote rows={overview?.hold_backtest ?? []} />

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
