/**
 * The AI 存股 verdict: a button, and what came back.
 *
 * Same gating as `AiVerdict` -- a request costs money, so nothing is fetched on
 * mount and the result is written into the react-query cache so reopening the
 * page shows what was already paid for. Read that file for why each of those is
 * there; only the differences are written down here.
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
 * **Card layout only.** The technical verdict also renders inline under a
 * watchlist row; this one does not. A holding assessment is not a thing anyone
 * scans twenty of, and putting a paid button on every board row for it would
 * spend a day's allowance on one scroll.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AiHoldAnalysisResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import type { MessageKey } from '../i18n'
import { errorMessage } from '../utils/errors'
import { HoldSuitabilityChip, SUITABILITY_KEY } from './ChenHoldCard'
import SignInPrompt from './SignInPrompt'

const CONFIDENCE_KEY: Record<'high' | 'medium' | 'low', MessageKey> = {
  high: 'ai.confidenceHigh',
  medium: 'ai.confidenceMedium',
  low: 'ai.confidenceLow',
}

export default function HoldAiVerdict({ sid }: { sid: string }) {
  const { t, locale, intlTag } = useI18n()
  const { status } = useAuth()
  const queryClient = useQueryClient()

  const cacheKey = ['hold-verdict', sid, locale]
  const [result, setResult] = useState<AiHoldAnalysisResponse | null>(
    () => queryClient.getQueryData<AiHoldAnalysisResponse>(cacheKey) ?? null,
  )

  // A verdict is prose in the language it was asked for, so switching language
  // leaves text on screen the rest of the page no longer matches. Swap in
  // whatever was already paid for in the new language, otherwise fall back to
  // the button. Adjusted during render rather than in an effect, so the
  // mismatched text never reaches the screen.
  const [shownLocale, setShownLocale] = useState(locale)
  if (shownLocale !== locale) {
    setShownLocale(locale)
    setResult(queryClient.getQueryData<AiHoldAnalysisResponse>(cacheKey) ?? null)
  }

  // Same key the technical card uses: one allowance, one number on screen.
  const quota = useQuery({
    queryKey: ['ai-quota'],
    queryFn: api.getAiQuota,
    enabled: status === 'authenticated',
    staleTime: 60_000,
  })

  const run = useMutation({
    mutationFn: () => api.generateHoldAnalysis(sid, locale),
    onSuccess: (data) => {
      setResult(data)
      queryClient.setQueryData(cacheKey, data)
      if (!data.cached) queryClient.invalidateQueries({ queryKey: ['ai-quota'] })
    },
  })

  const title = (
    <h2 className="card-title" style={{ margin: 0 }}>
      {t('holdAi.title')}
    </h2>
  )

  if (status !== 'authenticated') {
    return (
      <section className="card">
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
    <section className="card">
      <div className="row-between wrap" style={{ marginBottom: 12 }}>
        {title}
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
            {run.isPending
              ? t('holdAi.running')
              : result
                ? t('holdAi.rerun')
                : t('holdAi.run')}
          </button>
        </div>
      </div>

      {unavailable && <p className="banner-warn ai-note">{t('ai.unavailable')}</p>}
      {insufficient && <p className="banner-warn ai-note">{t('holdAi.insufficient')}</p>}
      {exhausted && !result && (
        <p className="banner-warn ai-note">{t('ai.quotaSpent', { time: resetsAt })}</p>
      )}
      {error && !unavailable && !insufficient && !exhausted && (
        <p className="banner-error ai-note">
          {t('ai.failed', { message: errorMessage(error, t) })}
        </p>
      )}

      {!result && !run.isPending && !error && <p className="dim">{t('holdAi.empty')}</p>}

      {result && <HoldVerdictBody result={result} />}
    </section>
  )
}

function HoldVerdictBody({ result }: { result: AiHoldAnalysisResponse }) {
  const { t, intlTag } = useI18n()
  const { verdict, rules } = result

  // Three states, not two. "The checklist could not score this" is a different
  // message from "the checklist disagreed", and collapsing them would credit
  // the model with contradicting a verdict nobody reached.
  const comparison =
    rules.suitability === null
      ? t('holdAi.noRuleVerdict')
      : verdict.agrees_with_rules
        ? t('holdAi.agreesWithRules', { label: t(SUITABILITY_KEY[rules.suitability]) })
        : t('holdAi.differsFromRules', { label: t(SUITABILITY_KEY[rules.suitability]) })

  return (
    <>
      <HoldSuitabilityChip suitability={verdict.suitability} />

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {verdict.headline}
      </p>

      <p className="dim" style={{ margin: '10px 0 0' }}>
        {t('ai.confidence')}：{t(CONFIDENCE_KEY[verdict.confidence])}
        {' · '}
        {comparison}
        {' · '}
        {t('ai.asOf', { date: result.as_of })}
      </p>

      {verdict.reasons.length > 0 && (
        <ul className="reason-list">
          {verdict.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {verdict.risks.length > 0 && (
        <>
          <span className="ai-block-title">{t('ai.risks')}</span>
          <ul className="reason-list ai-risks">
            {verdict.risks.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
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

      <p className="dim ai-disclaimer">{t('holdAi.disclaimer')}</p>
    </>
  )
}
