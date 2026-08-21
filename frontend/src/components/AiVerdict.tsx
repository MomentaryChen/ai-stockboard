/**
 * The AI position call: a button, and what came back.
 *
 * Deliberately not a query that runs on mount. Every other panel in this app
 * fetches as soon as it is rendered, because every other endpoint answers from
 * a cache that costs nothing to read. This one can spend a Gemini request, so
 * the user has to ask -- and a watchlist of twenty stocks must not turn into
 * twenty generations because somebody opened the page.
 *
 * Two layouts, one component, because the verdict is the same object in both:
 *
 * - `spotlight` is the 個股 band directly under the price header: full page
 *   width, above the chart, tinted and edged in the accent so it reads as a
 *   named region of the page rather than one more card in the analysis column.
 * - `inline` is the block under a watchlist quote, between the 四大買賣點 chip
 *   and 開高低收/五檔. Not a card, because a card there is a box inside a box;
 *   an accent rule and a wash say "this is the AI's answer" without the chrome.
 *
 * (There was a third, a plain `.card` that sat in the 個股 analysis column
 * looking like 四大買賣點' neighbour. The band replaced it: a card that looks
 * like every other card is exactly what a headline feature must not be.)
 *
 * Both used to sit *below* the evidence they judge. That order was
 * protecting the wallet -- free deterministic answer first, metered one after
 * -- but it was protecting it in the wrong place: the panel still generates
 * only on a click, so being read first is not the same as being paid for
 * first. What the old order actually cost was that the feature this product
 * leads with was the last thing on the page. The rule engine's verdict is
 * still on screen next to it either way: the 四大買賣點 chip sits above the
 * inline panel, and the analysis stack sits below the band.
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

import { ApiError, api } from '../api/client'
import type { AiAnalysisResponse, AiVerdict as Verdict } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { translateBfpLabel, useI18n } from '../i18n'
import type { MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'
import SignInPrompt from './SignInPrompt'

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

function callKey(verdict: Verdict): MessageKey {
  if (verdict.action === 'hold') return 'ai.callHold'
  return CALL_KEY[`${verdict.action}:${verdict.size}`] ?? 'ai.callHold'
}

export function AiCallChip({
  verdict,
  compact = true,
}: {
  verdict: Verdict
  compact?: boolean
}) {
  const { t } = useI18n()
  const pips = verdict.size ? PIPS[verdict.size] : 0

  return (
    <div className={`signal ${compact ? 'signal-sm' : ''} ${SIGNAL_CLASS[verdict.action]}`}>
      {t(callKey(verdict))}
      {pips > 0 && (
        <span className="ai-pips" aria-hidden="true">
          {'●'.repeat(pips)}
          <span className="ai-pips-off">{'●'.repeat(3 - pips)}</span>
        </span>
      )}
    </div>
  )
}

type Layout = 'inline' | 'spotlight'

/** One wrapper class per layout -- see the note at the top of the file. */
const WRAPPER_CLASS: Record<Layout, string> = {
  inline: 'ai-panel',
  spotlight: 'card ai-spotlight',
}

interface Props {
  sid: string
  layout?: Layout
}

export default function AiVerdictSection({ sid, layout = 'inline' }: Props) {
  const { t, locale, intlTag } = useI18n()
  const { status } = useAuth()
  const queryClient = useQueryClient()
  const spotlight = layout === 'spotlight'

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

  const title = (
    <h2 className="card-title" style={{ margin: 0 }}>
      {/* Only the band has to announce itself across the width of a page; the
          other two are already inside something that says where they are. */}
      {spotlight && (
        <span className="ai-mark" aria-hidden="true">
          ✦
        </span>
      )}
      {t('ai.title')}
    </h2>
  )

  if (status !== 'authenticated') {
    return (
      <section className={WRAPPER_CLASS[layout]}>
        <div className="row-between wrap" style={{ marginBottom: 12 }}>
          {title}
        </div>
        <SignInPrompt compact title={t('ai.signIn')} />
      </section>
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
    <section className={WRAPPER_CLASS[layout]}>
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        <div className="row wrap" style={{ gap: 8 }}>
          {title}
          {/* The call rides in the header beside the title, at the size the
              layout can carry -- full size in the band, where the call *is*
              the headline; a chip in a board row, where it is a line. */}
          {result && <AiCallChip verdict={result.verdict} compact={!spotlight} />}
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          {!exhausted && left !== null && !result && (
            <span className="dim ai-quota">{t('ai.quotaLeft', { left: String(left) })}</span>
          )}
          {run.isPending && <span className="spinner" />}
          <button
            type="button"
            className={`btn btn-sm${result || exhausted ? '' : ' btn-primary'}`}
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
          {t('ai.failed', { message: errorMessage(error, t) })}
        </p>
      )}

      {!result && !run.isPending && !error && <p className="dim ai-lead">{t('ai.empty')}</p>}

      {result && <AiVerdictBody result={result} wide={spotlight} />}
    </section>
  )
}

function AiVerdictBody({
  result,
  wide,
}: {
  result: AiAnalysisResponse
  /** The band is as wide as the page, so reasons and risks stand side by side
   *  instead of running one after the other down a board row. */
  wide: boolean
}) {
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

  // Named here rather than inlined twice: the two-column band and the
  // single-column panel differ only in how these are arranged.
  const reasons = verdict.reasons.length > 0 && (
    <>
      {/* A heading only where a bare list would be ambiguous -- in one column
          the list follows the summary and can only be its reasoning. */}
      {wide && <span className="ai-block-title">{t('ai.reasons')}</span>}
      <ul className="reason-list">
        {verdict.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </>
  )

  const risks = verdict.risks.length > 0 && (
    <>
      <span className="ai-block-title">{t('ai.risks')}</span>
      <ul className="reason-list ai-risks">
        {verdict.risks.map((risk) => (
          <li key={risk}>{risk}</li>
        ))}
      </ul>
    </>
  )

  return (
    <>
      {/* The call itself is in the header, next to the title -- the body picks
          up at the sentence explaining it. */}
      <p className="ai-headline dim" style={{ margin: 0 }}>
        {verdict.headline}
      </p>

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {t('ai.confidence')}：{t(CONFIDENCE_KEY[verdict.confidence])}
        {' · '}
        {agrees
          ? t('ai.agreesWithRule', { label: ruleLabel })
          : t('ai.differsFromRule', { label: ruleLabel })}
        {' · '}
        {t('ai.asOf', { date: result.as_of })}
      </p>

      {wide ? (
        <div className="ai-columns">
          <div>{reasons}</div>
          <div>{risks}</div>
        </div>
      ) : (
        <>
          {reasons}
          {risks}
        </>
      )}

      <p className="dim" style={{ margin: '12px 0 0', fontSize: 11 }}>
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
