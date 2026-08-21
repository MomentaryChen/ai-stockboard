import { Component, type ErrorInfo, type ReactNode } from 'react'

import { useI18n, type Translate } from '../i18n'

interface Props {
  t: Translate
  /** Changing this clears a caught error. The router hands it the pathname, so
   *  navigating away from a page that threw is a way out -- without it the
   *  boundary keeps rendering the fallback over whatever route comes next, and
   *  the only escape left is the reload the boundary exists to avoid. */
  resetKey: string
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * The last stop before a blank page.
 *
 * React unmounts the whole tree when a render throws and nothing catches it,
 * so before this existed one bad value anywhere below took the entire site
 * with it: a NaN reaching Recharts' custom candlestick shape, a `quote!`
 * assertion in useLiveQuote meeting the null it promised could not happen, an
 * unfamiliar payload shape in openIntel's three-source normalisation. All of
 * those are one component's problem and none of them should cost the user
 * their session -- which is what a hard reload does, since it drops the
 * in-memory query cache and re-runs session restore.
 *
 * It is a class because `getDerivedStateFromError` has no hook equivalent;
 * that is still true in React 19. The translate function is a prop rather than
 * a `useI18n()` call for the same reason, hence the wrapper below.
 */
class Boundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The stack is the only record of what actually broke -- the fallback
    // deliberately shows the user a sentence rather than a component trace.
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const { t } = this.props
    return (
      <div className="card crash">
        <h2 className="crash-title">{t('crash.title')}</h2>
        <p className="crash-lede">{t('crash.lede')}</p>
        <div className="row wrap">
          {/* Re-mounting the subtree is enough whenever the cause was a value
              that has since changed -- a stale quote, a query that has since
              refetched. Reload is the fallback, not the first offer. */}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => this.setState({ error: null })}
          >
            {t('crash.retry')}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => window.location.reload()}
          >
            {t('crash.reload')}
          </button>
        </div>
        {/* Collapsed, because it is for the bug report rather than the reader. */}
        <details className="crash-detail">
          <summary>{t('crash.details')}</summary>
          <pre>{error.message}</pre>
        </details>
      </div>
    )
  }
}

export default function ErrorBoundary({
  resetKey,
  children,
}: Omit<Props, 't'>) {
  const { t } = useI18n()
  return (
    <Boundary t={t} resetKey={resetKey}>
      {children}
    </Boundary>
  )
}
