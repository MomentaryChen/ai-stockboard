/**
 * The AI 存股 verdict: which assessment you are looking at, and a button for it.
 *
 * Same gating as `AiVerdict` -- generation costs money and waits for a click,
 * while reading what has already been generated is free and runs on mount.
 * Read that file for why each of those is there; only the differences are
 * written down here.
 *
 * **The quota is one number shared with the technical card.** Both read the
 * same `['ai-quota']` key, and the server counts one allowance across both
 * tables, so pressing either button moves the count under both. Two separate
 * counters would have been a lie about what an account may spend.
 *
 * **The comparison is against the checklist, not the 四大買賣點.** The rule
 * engine this verdict is put beside is `ChenHoldCard`, and the two share a
 * vocabulary -- strong/ok/weak/avoid -- so agreement can be stated plainly
 * rather than translated across two scales the way the technical card has to.
 *
 * **Two depths, two answers, three cache slots** -- the shape `AiVerdict`
 * arrived at, and this lane grew the same way. 價量 here is the checklist's own
 * snapshot; 深度 additionally reads the year-by-year earnings and payout record,
 * where today's PE and yield sit in this stock's own band, and institutional
 * flow. They can disagree, and a reader has to be able to see both -- which
 * needs the free read this lane went without until now, because two assessments
 * you can only reach by paying are not two assessments anyone will compare.
 *
 * Two layouts, matching the technical verdict's split:
 *
 * - `spotlight` is the 個股 band under the Hold analysis mode: full page
 *   width, same accent treatment as the trade AI band, so switching modes
 *   swaps which question the stage is answering rather than stacking both.
 * - `card` is the plain card used wherever a narrower column still hosts it.
 *
 * No board-row layout: a holding assessment is not a thing anyone scans
 * twenty of, and putting a paid button on every row would spend a day's
 * allowance on one scroll.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AiDepth, AiHoldAnalysisResponse, HoldDeepInputs } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import { GAP_KEY } from '../i18n/coverageGaps'
import type { MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'
import { HoldSuitabilityChip, SUITABILITY_KEY } from './ChenHoldCard'
import SignInPrompt from './SignInPrompt'

const CONFIDENCE_KEY: Record<'high' | 'medium' | 'low', MessageKey> = {
  high: 'ai.confidenceHigh',
  medium: 'ai.confidenceMedium',
  low: 'ai.confidenceLow',
}

type Layout = 'card' | 'spotlight'

const WRAPPER_CLASS: Record<Layout, string> = {
  card: 'card',
  spotlight: 'card ai-spotlight',
}

/** Read once an hour at most, like the technical verdict: a hold assessment is
 *  keyed on the trading day, and the only thing that can change it within a
 *  session is this user pressing the button, which writes the cache directly. */
const VERDICT_STALE_MS = 60 * 60 * 1000

/** The slot a verdict lives in. Bare for "whichever has been paid for", one per
 *  depth for "this named assessment" -- see the note in `AiVerdict`. */
function holdVerdictKey(sid: string, locale: string, depth?: AiDepth) {
  return depth ? ['hold-verdict', sid, locale, depth] : ['hold-verdict', sid, locale]
}

export default function HoldAiVerdict({
  sid,
  layout = 'card',
}: {
  sid: string
  layout?: Layout
}) {
  const { t, locale, intlTag } = useI18n()
  const { status } = useAuth()
  const queryClient = useQueryClient()
  const spotlight = layout === 'spotlight'

  // Which assessment the reader is looking at, or null for "whichever exists".
  // (locale, sid) is part of every key below, which is what makes a language
  // switch correct for free: the key changes, and whatever was already paid for
  // in the new language is what renders -- rather than prose in the language
  // the rest of the page has just stopped speaking.
  const [lane, setLane] = useState<AiDepth | null>(null)

  const restingKey = holdVerdictKey(sid, locale)

  const stored = useQuery({
    queryKey: restingKey,
    queryFn: () => api.getHoldAnalysis(sid, locale),
    enabled: status === 'authenticated' && lane === null,
    staleTime: VERDICT_STALE_MS,
  })

  const picked = useQuery({
    queryKey: holdVerdictKey(sid, locale, lane ?? 'quick'),
    queryFn: () => api.getHoldAnalysis(sid, locale, lane ?? 'quick'),
    enabled: status === 'authenticated' && lane !== null,
    staleTime: VERDICT_STALE_MS,
  })

  const active = lane === null ? stored : picked
  const result = active.data ?? null
  const reading = active.isLoading

  const shownDepth: AiDepth = lane ?? result?.depth ?? 'quick'

  // Same key the technical card uses: one allowance, one number on screen.
  const quota = useQuery({
    queryKey: ['ai-quota'],
    queryFn: api.getAiQuota,
    enabled: status === 'authenticated',
    staleTime: 60_000,
  })

  const run = useMutation({
    mutationFn: (depth: AiDepth) => api.generateHoldAnalysis(sid, locale, depth),
    onSuccess: (data) => {
      setLane(data.depth)
      queryClient.setQueryData(holdVerdictKey(sid, locale, data.depth), data)
      // The bare slot means "the best answer available", so a deep verdict
      // belongs in it and a quick one does not.
      if (data.depth === 'deep') queryClient.setQueryData(restingKey, data)
      if (!data.cached) queryClient.invalidateQueries({ queryKey: ['ai-quota'] })
    },
  })

  const title = (
    <h2 className="card-title" style={{ margin: 0 }}>
      {spotlight && (
        <span className="ai-mark" aria-hidden="true">
          ✦
        </span>
      )}
      {t('holdAi.title')}
    </h2>
  )

  if (status !== 'authenticated') {
    return (
      <section className={WRAPPER_CLASS[layout]}>
        <div className="row-between wrap" style={{ marginBottom: 12 }}>
          {title}
        </div>
        <SignInPrompt compact title={t('holdAi.signIn')} />
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
          {result && <HoldSuitabilityChip suitability={result.verdict.suitability} />}
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          {!exhausted && left !== null && !result && !reading && (
            <span className="dim ai-quota">{t('ai.quotaLeft', { left: String(left) })}</span>
          )}
          {(run.isPending || reading) && <span className="spinner" />}
          {/* Selecting a lane spends nothing -- it re-reads a cache-only
              endpoint. The button beside it is the only control here that can
              cost anything, and it names the lane it would pay for. */}
          <div className="segmented" role="group" aria-label={t('ai.depthSelect')}>
            {(['quick', 'deep'] as const).map((depth) => (
              <button
                key={depth}
                type="button"
                className={`btn btn-sm ${shownDepth === depth ? 'active' : ''}`}
                aria-pressed={shownDepth === depth}
                disabled={run.isPending}
                onClick={() => setLane(depth)}
              >
                {t(depth === 'deep' ? 'ai.depthDeep' : 'holdAi.depthQuick')}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`btn btn-sm${result || exhausted ? '' : ' btn-primary'}`}
            // Disabled while the free read is in flight, for the reason the
            // technical panel gives: until it lands we do not know whether this
            // click would spend anything.
            disabled={run.isPending || reading || (exhausted && !result)}
            onClick={() => run.mutate(shownDepth)}
          >
            {run.isPending
              ? t(shownDepth === 'deep' ? 'holdAi.runningDeep' : 'holdAi.running')
              : result
                ? t(shownDepth === 'deep' ? 'holdAi.rerunDeep' : 'holdAi.rerun')
                : t(shownDepth === 'deep' ? 'holdAi.runDeep' : 'holdAi.run')}
          </button>
        </div>
      </div>

      {unavailable && <p className="banner-warn ai-note">{t('ai.unavailable')}</p>}
      {insufficient && <p className="banner-warn ai-note">{t('holdAi.insufficient')}</p>}
      {exhausted && !result && !reading && (
        <p className="banner-warn ai-note">{t('ai.quotaSpent', { time: resetsAt })}</p>
      )}
      {error && !unavailable && !insufficient && !exhausted && (
        <p className="banner-error ai-note">
          {t('ai.failed', { message: errorMessage(error, t) })}
        </p>
      )}

      {/* "尚未評估" is a claim about the cache, so it waits for the cache to
          answer -- otherwise the panel asserts it for a moment on mount and
          then contradicts itself. */}
      {!result && !run.isPending && !reading && !error && (
        <>
          <p className={`dim${spotlight ? ' ai-lead' : ''}`} style={{ marginBottom: 4 }}>
            {t(shownDepth === 'deep' ? 'holdAi.emptyDeep' : 'holdAi.empty')}
          </p>
          {/* Said before the button is pressed: what the deep call reads is the
              thing worth knowing while choosing between the two. */}
          <p className={`dim${spotlight ? ' ai-lead' : ''}`} style={{ marginTop: 0 }}>
            {t('holdAi.deepLead')}
          </p>
        </>
      )}

      {result && <HoldVerdictBody result={result} wide={spotlight} />}
    </section>
  )
}

function HoldVerdictBody({
  result,
  wide,
}: {
  result: AiHoldAnalysisResponse
  /** The band is page-wide, so reasons and risks stand side by side. */
  wide: boolean
}) {
  const { t, intlTag } = useI18n()
  const { verdict, rules, deep } = result

  // Three states, not two. "The checklist could not score this" is a different
  // message from "the checklist disagreed", and collapsing them would credit
  // the model with contradicting a verdict nobody reached.
  const comparison =
    rules.suitability === null
      ? t('holdAi.noRuleVerdict')
      : verdict.agrees_with_rules
        ? t('holdAi.agreesWithRules', { label: t(SUITABILITY_KEY[rules.suitability]) })
        : t('holdAi.differsFromRules', { label: t(SUITABILITY_KEY[rules.suitability]) })

  const reasons = verdict.reasons.length > 0 && (
    <>
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
      {/* Suitability rides in the header beside the title when spotlighted;
          the body starts at the headline explaining it. */}
      {!wide && <HoldSuitabilityChip suitability={verdict.suitability} />}

      <p className={`dim${wide ? ' ai-headline' : ''}`} style={{ margin: wide ? 0 : '10px 0 0' }}>
        {verdict.headline}
      </p>

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {t('ai.confidence')}：{t(CONFIDENCE_KEY[verdict.confidence])}
        {' · '}
        {/* Which assessment answered. The two can disagree, and a reader
            comparing them has to be able to tell which is which. */}
        {t(result.depth === 'deep' ? 'ai.depthDeep' : 'holdAi.depthQuick')}
        {' · '}
        {comparison}
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

      {deep && <HoldDeepEvidence deep={deep} />}

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

      <p className="dim ai-disclaimer">
        {t(result.depth === 'deep' ? 'holdAi.disclaimerDeep' : 'holdAi.disclaimer')}
      </p>
    </>
  )
}

/** What the deep call read, summarised beside the verdict it produced.
 *
 *  Not the whole series -- a ten-row table under a paragraph of prose is a
 *  second card nobody asked for, and the per-year figures are already on the
 *  page in `ChenHoldCard` and `DividendCard`. What is here is the part the
 *  reader cannot get anywhere else: how much record was actually behind the
 *  answer, and what was missing from it. */
function HoldDeepEvidence({ deep }: { deep: HoldDeepInputs }) {
  const { t } = useI18n()
  const { fundamentals, valuation } = deep

  const payout =
    fundamentals.avg_payout_ratio_pct === null
      ? null
      : t('holdAi.deepPayout', {
          pct: fundamentals.avg_payout_ratio_pct.toFixed(1),
        })

  // Suppressed on a short band for the same reason the prompt is told not to
  // cite it: a fortnight of sessions produces a percentile that looks exactly
  // like a decade's.
  const band =
    valuation.pe_percentile !== null &&
    !deep.coverage_gaps.includes('short_valuation_history')
      ? t('holdAi.deepBand', {
          pct: valuation.pe_percentile.toFixed(0),
          days: String(valuation.days_covered),
        })
      : null

  return (
    <>
      <p className="dim" style={{ margin: '10px 0 0', fontSize: 12 }}>
        {t('holdAi.deepIncluded', {
          years: String(fundamentals.years.length),
          days: String(deep.chip.days_covered),
        })}
        {payout && ` · ${payout}`}
        {band && ` · ${band}`}
      </p>

      {/* Named rather than left silent. A deep verdict drawn from a company
          with no payout archive is a checklist verdict wearing a deep label,
          and the reader is the only one who can decide whether that matters. */}
      {deep.coverage_gaps.length > 0 && (
        <>
          <span className="ai-block-title">{t('ai.gaps')}</span>
          <ul className="reason-list coverage-gaps">
            {deep.coverage_gaps.map((gap) => (
              // An unrecognised key falls through as itself rather than
              // rendering blank -- same contract as serverText.ts.
              <li key={gap}>{gap in GAP_KEY ? t(GAP_KEY[gap]) : gap}</li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}
