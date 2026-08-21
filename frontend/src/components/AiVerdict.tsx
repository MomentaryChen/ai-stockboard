/**
 * The AI position call: a button, and what came back.
 *
 * Deliberately not a query that runs on mount. Every other panel in this app
 * fetches as soon as it is rendered, because every other endpoint answers from
 * a cache that costs nothing to read. This one can spend a Gemini request, so
 * the user has to ask -- and a watchlist of twenty stocks must not turn into
 * twenty generations because somebody opened the page.
 *
 * The result is written into the react-query cache under (sid, locale) rather
 * than kept in local state alone, so collapsing a board row and opening it
 * again shows the verdict already paid for instead of offering the button
 * again. The server would have answered from its own cache anyway, but a round
 * trip that reports `cached: true` still looks like a second charge to anyone
 * watching the button spin.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { AiAnalysisResponse, AiVerdict as Verdict } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { translateBfpLabel, useI18n } from '../i18n'
import type { MessageKey } from '../i18n'

/** Which of the seven answers this is. Split by action so `hold` needs no size. */
const CALL_KEY: Record<string, MessageKey> = {
  'enter:large': 'ai.callEnterLarge',
  'enter:medium': 'ai.callEnterMedium',
  'enter:small': 'ai.callEnterSmall',
  'exit:large': 'ai.callExitLarge',
  'exit:medium': 'ai.callExitMedium',
  'exit:small': 'ai.callExitSmall',
}

const CONFIDENCE_KEY: Record<Verdict['confidence'], MessageKey> = {
  high: 'ai.confidenceHigh',
  medium: 'ai.confidenceMedium',
  low: 'ai.confidenceLow',
}

/** Reuses the 四大買賣點 palette: entering is the up colour, exiting the down
 *  one. Taiwan's convention is already encoded there, and a second scheme on
 *  the same screen would read as a second meaning. */
const SIGNAL_CLASS: Record<Verdict['action'], string> = {
  enter: 'signal-buy',
  exit: 'signal-sell',
  hold: 'signal-hold',
}

/** How emphatic the size is, as three pips. Text carries the meaning; this is
 *  for the eye scanning past it. */
const PIPS: Record<string, number> = { large: 3, medium: 2, small: 1 }

export function AiCallChip({ verdict }: { verdict: Verdict }) {
  const { t } = useI18n()
  const key =
    verdict.action === 'hold'
      ? 'ai.callHold'
      : CALL_KEY[`${verdict.action}:${verdict.size}`]
  const pips = verdict.size ? PIPS[verdict.size] : 0

  return (
    <span className={`signal signal-sm ${SIGNAL_CLASS[verdict.action]}`}>
      {t(key ?? 'ai.callHold')}
      {pips > 0 && (
        <span className="ai-pips" aria-hidden="true">
          {'●'.repeat(pips)}
          <span className="ai-pips-off">{'●'.repeat(3 - pips)}</span>
        </span>
      )}
    </span>
  )
}

interface Props {
  sid: string
}

export default function AiVerdictSection({ sid }: Props) {
  const { t, locale, intlTag } = useI18n()
  const { status } = useAuth()
  const { pathname } = useLocation()
  const queryClient = useQueryClient()

  const cacheKey = ['ai-verdict', sid, locale]
  const [result, setResult] = useState<AiAnalysisResponse | null>(
    () => queryClient.getQueryData<AiAnalysisResponse>(cacheKey) ?? null,
  )

  // A verdict is written by the model in the language it was asked for, so
  // switching language leaves prose on screen that the rest of the page no
  // longer matches. Swap in whatever was already paid for in the new language,
  // and otherwise fall back to the button. Adjusting state during render rather
  // than in an effect so the mismatched text never reaches the screen.
  const [shownLocale, setShownLocale] = useState(locale)
  if (shownLocale !== locale) {
    setShownLocale(locale)
    setResult(queryClient.getQueryData<AiAnalysisResponse>(cacheKey) ?? null)
  }

  // One shared request for the whole page: react-query dedupes on the key, so
  // the number on screen is right whichever row is open.
  const quota = useQuery({
    queryKey: ['ai-quota'],
    queryFn: api.getAiQuota,
    enabled: status === 'authenticated',
    staleTime: 60_000,
  })

  const run = useMutation({
    mutationFn: () => api.generateAiAnalysis(sid, locale),
    onSuccess: (data) => {
      setResult(data)
      queryClient.setQueryData(cacheKey, data)
      // Only a miss moves the counter, and the response is the only thing that
      // knows which it was -- so re-read rather than decrementing here.
      if (!data.cached) queryClient.invalidateQueries({ queryKey: ['ai-quota'] })
    },
  })

  if (status !== 'authenticated') {
    return (
      <div className="ai-panel">
        <p className="dim ai-empty">
          {t('ai.signIn')}{' '}
          <Link to="/login" state={{ from: pathname }}>
            {t('menu.login')}
          </Link>
        </p>
      </div>
    )
  }

  const error = run.error
  const unavailable = error instanceof ApiError && error.status === 503
  const insufficient = error instanceof ApiError && error.status === 422
  const exhausted =
    (error instanceof ApiError && error.status === 429) ||
    (quota.data !== undefined && quota.data.used >= quota.data.limit)

  const left = quota.data ? Math.max(0, quota.data.limit - quota.data.used) : null
  const resetsAt = quota.data
    ? new Date(quota.data.resets_at).toLocaleTimeString(intlTag, {
        hour: '2-digit',
        minute: '2-digit',
      })
    : ''

  return (
    <div className="ai-panel">
      <div className="row-between wrap ai-head">
        <div className="row" style={{ gap: 8 }}>
          <strong className="ai-title">{t('ai.title')}</strong>
          {result && <AiCallChip verdict={result.verdict} />}
        </div>
        <div className="row" style={{ gap: 8 }}>
          {!exhausted && left !== null && !result && (
            <span className="dim ai-quota">{t('ai.quotaLeft', { left: String(left) })}</span>
          )}
          <button
            type="button"
            className="btn-sm"
            disabled={run.isPending || (exhausted && !result)}
            onClick={() => run.mutate()}
          >
            {run.isPending ? t('ai.running') : result ? t('ai.rerun') : t('ai.run')}
          </button>
        </div>
      </div>

      {unavailable && <p className="banner-warn ai-note">{t('ai.unavailable')}</p>}
      {insufficient && <p className="banner-warn ai-note">{t('ai.insufficient')}</p>}
      {exhausted && !result && (
        <p className="banner-warn ai-note">{t('ai.quotaSpent', { time: resetsAt })}</p>
      )}
      {error && !unavailable && !insufficient && !exhausted && (
        <p className="banner-error ai-note">
          {t('ai.failed', { message: (error as Error).message })}
        </p>
      )}

      {!result && !run.isPending && !error && <p className="dim ai-empty">{t('ai.empty')}</p>}

      {result && <AiVerdictBody result={result} />}
    </div>
  )
}

function AiVerdictBody({ result }: { result: AiAnalysisResponse }) {
  const { t, intlTag } = useI18n()
  const { verdict } = result

  // The rule engine's own vocabulary is buy/sell/hold; the AI's is
  // enter/exit/hold. They line up only loosely, so the comparison is stated as
  // agreement or disagreement rather than by putting two labels side by side
  // and leaving the reader to work out whether they mean the same thing.
  const ruleSignal = result.traditional.signal
  const agrees =
    (verdict.action === 'enter' && ruleSignal === 'buy') ||
    (verdict.action === 'exit' && ruleSignal === 'sell') ||
    (verdict.action === 'hold' && ruleSignal === 'hold')
  const ruleLabel = translateBfpLabel(result.traditional.label, t)

  return (
    <>
      <p className="ai-headline">{verdict.headline}</p>

      <p className="dim ai-meta">
        {t('ai.confidence')}：{t(CONFIDENCE_KEY[verdict.confidence])}
        {' · '}
        {agrees
          ? t('ai.agreesWithRule', { label: ruleLabel })
          : t('ai.differsFromRule', { label: ruleLabel })}
      </p>

      {verdict.reasons.length > 0 && (
        <div className="ai-block">
          <span className="ai-block-title">{t('ai.reasons')}</span>
          <ul className="quote-bfp-reasons">
            {verdict.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {verdict.risks.length > 0 && (
        <div className="ai-block">
          <span className="ai-block-title">{t('ai.risks')}</span>
          <ul className="quote-bfp-reasons ai-risks">
            {verdict.risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="dim ai-foot">
        {t('ai.asOf', { date: result.as_of })}
        {' · '}
        {t('ai.generatedAt', {
          time: new Date(result.generated_at).toLocaleString(intlTag, {
            month: 'numeric',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          }),
        })}
        {result.cached && ` · ${t('ai.cached')}`}
        {' · '}
        {t('ai.modelNote', { model: result.model, version: result.prompt_version })}
      </p>

      <p className="dim ai-disclaimer">{t('ai.disclaimer')}</p>
    </>
  )
}
