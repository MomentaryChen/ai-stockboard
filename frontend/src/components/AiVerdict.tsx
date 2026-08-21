/**
 * The AI position call: what has already been decided, and a button to decide it.
 *
 * The panel reads on mount and writes only on a click, because those are two
 * different endpoints. GET /analysis/ai is cache-only -- it cannot reach Gemini
 * or the exchange -- so running it for every row of a board costs nothing and
 * is what lets a verdict somebody already paid for actually appear. POST is the
 * metered one, and it still waits for the button: twenty stocks on screen must
 * never become twenty generations because a page opened.
 *
 * That split is the whole feature. Before it existed the only way to discover a
 * stored verdict was to POST, so a board that had already been judged rendered
 * as though it never had, and every reader was shown a button whose answer was
 * sitting in the database.
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
 * (sid, locale) is the react-query key and the only place the verdict lives --
 * no local copy alongside it. That is what makes a locale switch correct for
 * free: the key changes, and whatever was already paid for in the new language
 * is what renders, rather than prose in the language the rest of the page has
 * just stopped speaking.
 *
 * Depth is in that key too, and it took a bug to get there. The server has
 * always stored the quick and deep verdicts as separate rows; this panel kept
 * both in one client slot and the metered route probed its cache without
 * naming a depth, so whichever answer existed was handed to whichever button
 * was pressed. Two controls, one answer, and no way to see the other -- which
 * is the opposite of what a second lane is for.
 *
 * So there are three slots (see `aiVerdictKey`): the bare key for "whatever has
 * been paid for", which is what a board reads and what a batching parent seeds,
 * and one per depth for "this named answer". A page that lands on a stock
 * somebody analysed deeply still shows the deep verdict without asking, because
 * the bare read is what runs on mount. Choosing a lane pins it from then on,
 * and costs nothing: the pinned read is the same cache-only endpoint with
 * `depth` named, so an unpaid lane can say so instead of borrowing the other
 * one's verdict.
 *
 * `batched` is for parents that fetch the whole basket themselves -- the card
 * board mounts a panel per row, and twenty single reads is the thing
 * /api/analysis/ai exists to collapse. Such a parent seeds this exact key, so
 * the panel below only has to stop asking; it reads the cache either way. The
 * convention is the one `bfp`/`bfpLoading` already set on the same boards.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ApiError, api } from '../api/client'
import type { AiAnalysisResponse, AiDepth, AiVerdict as Verdict } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { translateBfpLabel, useI18n } from '../i18n'
import { GAP_KEY } from '../i18n/coverageGaps'
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

/** Read once an hour at most. A verdict is keyed on the trading day, so within
 *  a session there is nothing newer to find -- and the only thing that *can*
 *  change it, this user pressing the button, writes the cache directly. */
const VERDICT_STALE_MS = 60 * 60 * 1000

/** The key both this panel and any batching parent write. Exported so a parent
 *  seeding the basket cannot drift from the panel reading it.
 *
 *  `depth` splits it into three slots, and the split is the point. The bare
 *  key holds "whatever has been paid for, better answer first" -- what a board
 *  read returns and what a batching parent seeds. The two depth keys hold one
 *  named answer each, which is what a panel showing 價量 has to keep showing
 *  even after somebody generates a 深度 verdict for the same day. One shared
 *  slot is exactly the bug this split fixes: the deep answer landed in it and
 *  the quick lane rendered it as its own. */
export function aiVerdictKey(sid: string, locale: string, depth?: AiDepth) {
  return depth ? ['ai-verdict', sid, locale, depth] : ['ai-verdict', sid, locale]
}

interface Props {
  sid: string
  layout?: Layout
  /** A parent has fetched the whole basket and seeded this key -- do not ask
   *  again. See the note at the top of the file. */
  batched?: boolean
  /** That parent's read is still in flight, so an empty cache is premature.
   *  Same pair as `bfp`/`bfpLoading` on the boards that pass both. */
  batchLoading?: boolean
}

export default function AiVerdictSection({
  sid,
  layout = 'inline',
  batched,
  batchLoading,
}: Props) {
  const { t, locale, intlTag } = useI18n()
  const { status } = useAuth()
  const queryClient = useQueryClient()
  const spotlight = layout === 'spotlight'

  const cacheKey = aiVerdictKey(sid, locale)

  // Which lane the reader is looking at, or null for "whichever was paid for".
  // Null is the resting state rather than 'quick', because a page landing on a
  // stock somebody analysed deeply should show that verdict without being
  // asked -- the deep answer is strictly better informed and nobody is charged
  // for reading it. Pressing either control pins the lane from then on.
  const [lane, setLane] = useState<AiDepth | null>(null)

  // Free, and therefore allowed to run on mount -- see the top of the file.
  // `null` is a real answer here ("nobody has generated one"), so it is cached
  // like any other rather than retried as a miss.
  const stored = useQuery({
    queryKey: cacheKey,
    queryFn: () => api.getAiAnalysis(sid, locale),
    enabled: status === 'authenticated' && !batched && lane === null,
    staleTime: VERDICT_STALE_MS,
  })

  // The pinned read. Also free -- it is the same cache-only endpoint with the
  // depth named -- which is what makes moving between the two answers cost
  // nothing and what lets an unpaid lane report itself as unpaid instead of
  // borrowing the other one's verdict.
  const picked = useQuery({
    queryKey: aiVerdictKey(sid, locale, lane ?? 'quick'),
    queryFn: () => api.getAiAnalysis(sid, locale, lane ?? 'quick'),
    enabled: status === 'authenticated' && lane !== null,
    staleTime: VERDICT_STALE_MS,
  })

  const active = lane === null ? stored : picked
  const result = active.data ?? null

  // `isLoading`, not `isPending`: a disabled query stays pending forever, and a
  // batched panel would show a spinner that never resolves. Whether the basket
  // is still coming is the parent's fact, so the parent states it -- but only
  // for the unpinned read it seeded; a pinned one is this panel's own request.
  const reading =
    lane === null ? (batched ? Boolean(batchLoading) : stored.isLoading) : picked.isLoading

  // Which lane the controls describe. Before anything has loaded there is
  // nothing better to claim than the cheap one; once a verdict is on screen the
  // selector has to agree with the meta line under it.
  const shownDepth: AiDepth = lane ?? result?.depth ?? 'quick'

  // One shared request for the whole page: react-query dedupes on the key, so
  // the number on screen is right whichever row is open.
  const quota = useQuery({
    queryKey: ['ai-quota'],
    queryFn: api.getAiQuota,
    enabled: status === 'authenticated',
    staleTime: 60_000,
  })

  const run = useMutation({
    mutationFn: (depth: AiDepth) => api.generateAiAnalysis(sid, locale, depth),
    onSuccess: (data) => {
      // The cache is the only copy, so this is the whole of "show the result".
      setLane(data.depth)
      queryClient.setQueryData(aiVerdictKey(sid, locale, data.depth), data)
      // The unpinned slot means "the best answer available", so a deep verdict
      // belongs in it and a quick one does not: writing a quick verdict there
      // would make the next mount show the shallower of two paid-for answers.
      if (data.depth === 'deep') queryClient.setQueryData(cacheKey, data)
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
          {!exhausted && left !== null && !result && !reading && (
            <span className="dim ai-quota">{t('ai.quotaLeft', { left: String(left) })}</span>
          )}
          {(run.isPending || reading) && <span className="spinner" />}
          {/* A selector, and then one button that acts on what is selected.
              The previous shape was two buttons, on the reasoning that a toggle
              makes an expensive call reachable by a control that looks like a
              view setting. That reasoning survives; this control is not that
              toggle. Switching lanes here spends nothing -- it re-reads a
              cache-only endpoint -- and the metered click stays a labelled
              button that names the lane it is about to pay for. What the two
              buttons could not express is the thing that was actually wrong:
              that these are two answers, only one of which is on screen. */}
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
                {t(depth === 'deep' ? 'ai.depthDeep' : 'ai.depthQuick')}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`btn btn-sm${result || exhausted ? '' : ' btn-primary'}`}
            // Disabled while the free read is in flight too: until it lands we
            // do not know whether this click would spend anything, and offering
            // "開始評估" over a verdict that is about to appear invites paying
            // for one that was already there.
            disabled={run.isPending || reading || (exhausted && !result)}
            onClick={() => run.mutate(shownDepth)}
          >
            {run.isPending
              ? t(shownDepth === 'deep' ? 'ai.runningDeep' : 'ai.running')
              : result
                ? t(shownDepth === 'deep' ? 'ai.rerunDeep' : 'ai.rerun')
                : t(shownDepth === 'deep' ? 'ai.runDeep' : 'ai.run')}
          </button>
        </div>
      </div>

      {unavailable && <p className="banner-warn ai-note">{t('ai.unavailable')}</p>}
      {insufficient && <p className="banner-warn ai-note">{t('ai.insufficient')}</p>}
      {exhausted && !result && !reading && (
        <p className="banner-warn ai-note">{t('ai.quotaSpent', { time: resetsAt })}</p>
      )}
      {error && !unavailable && !insufficient && !exhausted && (
        <p className="banner-error ai-note">
          {t('ai.failed', { message: errorMessage(error, t) })}
        </p>
      )}

      {/* "尚未評估" is a claim about the cache, so it waits for the cache to
          answer -- otherwise every panel asserts it for a moment on mount and
          then contradicts itself. */}
      {!result && !run.isPending && !reading && !error && (
        <>
          <p className="dim ai-lead" style={{ marginBottom: 4 }}>
            {t(shownDepth === 'deep' ? 'ai.emptyDeep' : 'ai.empty')}
          </p>
          {/* Said before the button is pressed, not after: what the deep call
              reads is the thing worth knowing while choosing between them. */}
          <p className="dim ai-lead" style={{ marginTop: 0 }}>
            {t('ai.deepLead')}
          </p>
        </>
      )}

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
  const { verdict, deep } = result

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
        {/* Which depth answered. Two verdicts for one stock can disagree, and
            a reader comparing them has to be able to tell which is which. */}
        {t(result.depth === 'deep' ? 'ai.depthDeep' : 'ai.depthQuick')}
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

      {deep && (
        <p className="dim" style={{ margin: '10px 0 0', fontSize: 12 }}>
          {t('ai.deepIncluded', { days: String(deep.chip.days_covered) })}
        </p>
      )}

      {/* Named rather than left silent. A deep verdict drawn from a stock with
          no institutional report is a quick verdict wearing a deep label, and
          the reader is the only one who can decide whether that matters. */}
      {deep && deep.coverage_gaps.length > 0 && (
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

      {/* The quick text says the verdict has no institutional flow or
          fundamentals in it. On a deep call that is simply untrue, and a
          disclaimer that misstates what was read is worse than none. */}
      <p className="dim ai-disclaimer">
        {t(deep ? 'ai.disclaimerDeep' : 'ai.disclaimer')}
      </p>
    </>
  )
}
