import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ErrorBoundary from './ErrorBoundary'
import { I18nProvider } from '../i18n'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  // React logs every caught error itself, and so does componentDidCatch. The
  // point of these tests is what the user is left looking at.
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

function Boom(): ReactNode {
  throw new Error('candlestick shape received a NaN')
}

function render(node: ReactNode) {
  act(() => {
    root.render(<I18nProvider>{node}</I18nProvider>)
  })
}

describe('ErrorBoundary', () => {
  it('shows a fallback instead of unmounting the tree', () => {
    render(
      <ErrorBoundary resetKey="/">
        <Boom />
      </ErrorBoundary>,
    )

    // Before this existed, the whole app came out as an empty <div>.
    expect(container.querySelector('.crash')).not.toBeNull()
    expect(container.textContent).not.toBe('')
    // The message is kept for the bug report, not thrown away.
    expect(container.textContent).toContain('candlestick shape received a NaN')
  })

  it('keeps rendering children when nothing throws', () => {
    render(
      <ErrorBoundary resetKey="/">
        <p className="fine">board</p>
      </ErrorBoundary>,
    )

    expect(container.querySelector('.crash')).toBeNull()
    expect(container.querySelector('.fine')?.textContent).toBe('board')
  })

  it('clears the fallback when resetKey changes, so navigating away is a way out', () => {
    render(
      <ErrorBoundary resetKey="/stock/2330">
        <Boom />
      </ErrorBoundary>,
    )
    expect(container.querySelector('.crash')).not.toBeNull()

    // What the router does on navigation: same boundary, new pathname.
    render(
      <ErrorBoundary resetKey="/">
        <p className="fine">board</p>
      </ErrorBoundary>,
    )

    expect(container.querySelector('.crash')).toBeNull()
    expect(container.querySelector('.fine')?.textContent).toBe('board')
  })

  it('retries the subtree when the reader asks, without a page reload', () => {
    // The cause of a render error is often a value that has since changed, so
    // re-mounting is offered before the reload that costs the query cache.
    let shouldThrow = true
    function Flaky() {
      if (shouldThrow) throw new Error('transient')
      return <p className="fine">recovered</p>
    }

    render(
      <ErrorBoundary resetKey="/">
        <Flaky />
      </ErrorBoundary>,
    )
    expect(container.querySelector('.crash')).not.toBeNull()

    shouldThrow = false
    const retry = container.querySelector<HTMLButtonElement>('.btn-primary')
    expect(retry).not.toBeNull()
    act(() => retry!.click())

    expect(container.querySelector('.crash')).toBeNull()
    expect(container.querySelector('.fine')?.textContent).toBe('recovered')
  })
})
