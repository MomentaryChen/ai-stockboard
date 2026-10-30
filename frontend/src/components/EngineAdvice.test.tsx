/**
 * The words a reader acts on. A wrong branch here would still render a tidy
 * sentence, so what matters is that each engine answer maps to the right one:
 * no entry must never read as a buy, and a weak grade must never read as a sell.
 */

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import EngineAdvice from './EngineAdvice'
import type { EngineEntry, EnginePlan, EngineRecommendation } from '../api/types'
import { I18nProvider } from '../i18n'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  localStorage.setItem('ai-stockboard.locale', 'zh-TW')
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const entry = (overrides: Partial<EngineEntry> = {}): EngineEntry => ({
  k_atr: 0,
  price: 989,
  ev: 0.05,
  ev_lo: 0.01,
  win_rate: 0.6,
  worst_5pct: -0.2,
  fill_rate: 1,
  max_weight: 0.024,
  ...overrides,
})

const plan = (planEntry: EngineEntry | null): EnginePlan => ({
  ev: 0.03,
  worst_5pct: -0.2,
  trust: 'low',
  entry: planEntry,
})

const item = (overrides: Partial<EngineRecommendation> = {}): EngineRecommendation => ({
  sid: '3037',
  name: '欣興',
  market: '上市',
  sector: '電子零組件',
  sector_rank: 3,
  market_cap: 1,
  status: 'ok',
  close: 989,
  as_of: '2026-10-30',
  short: plan(entry()),
  long: plan(entry()),
  hold_state: 'strong',
  risk_line: 783,
  ...overrides,
})

function render(value: EngineRecommendation) {
  act(() => {
    root.render(
      <MemoryRouter>
        <I18nProvider>
          <EngineAdvice item={value} />
        </I18nProvider>
      </MemoryRouter>,
    )
  })
  return container.textContent ?? ''
}

describe('EngineAdvice', () => {
  it('says buy near the current price at the top level, with the money example', () => {
    const text = render(item({ long: plan(null) }))
    expect(text).toContain('現價附近可進')
    expect(text).toContain('平均約賺 5,000 元')
  })

  it('shows the bad case as one negative figure', () => {
    const text = render(item())
    expect(text).toContain('-20.0%')
    expect(text).not.toContain('--')
  })

  it('names the price when it has to fall first', () => {
    expect(render(item({ short: plan(entry({ k_atr: 2, price: 901.5 })) }))).toContain('跌到 901.50 以下再進')
  })

  it('never reads as a buy when there is no entry level', () => {
    const text = render(item({ short: plan(null), long: plan(null) }))
    expect(text).toContain('不建議買')
    expect(text).not.toContain('可進')
  })

  it('does not tell a holder of a weak stock to sell', () => {
    const text = render(item({ hold_state: 'weak' }))
    expect(text).toContain('偏弱')
    expect(text).toContain('優先賣')
    expect(text).not.toContain('建議賣出')
  })

  it('shows no advice for a stock that could not be scored', () => {
    const text = render(item({ status: 'failed', short: null, long: null, hold_state: 'unknown', risk_line: null }))
    expect(text).toContain('暫時無法評估')
    expect(text).not.toContain('不建議買')
  })
})
