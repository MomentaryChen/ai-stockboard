# Chen Zhongming hold analysis — product & engineering plan

Branch: `feat/chen-hold-analysis`  
Worktree: `…/worktree/ai-stockboard--feat-chen-hold-analysis`  
Status: **M1 shipped.** M2 (fundamentals ingest) and M3 (hold backtest) are
still open; the checklist below records what each milestone covers and what
M1 actually decided.

## Goal

Add a **fundamentals / 存股** analysis lane that can sit beside the existing
technical engines and be compared honestly (“平測”): same stock, same history,
different questions and scorecards.

The public methodology to encode is **陳重銘存股術** (pick a good company, buy
when cheap, hold long, reinvest dividends). That is not a short-horizon buy/sell
signal. It must not be folded into the current Gemini position call
(`enter` / `exit` / `hold` + size).

## What we have today

| Engine | Question | Scorecard |
|--------|----------|-----------|
| Traditional (Best Four Point) | Is there a short-horizon volume/price signal today? | Signal replay vs buy-and-hold (done) |
| Technical AI | Enter / exit / hold and size? | No replay yet (README gap) |
| Dividends card | What cash was paid; TTM yield display | Display only — not used for screening or AI |

There is **no** EPS / ROE / PE store, no board-wide dividend prefill job, and no
fundamentals AI. Industry is only `stock_code.group`.

## Product shape: four engines, two scorecards

```
                    ┌─────────────────────────────┐
  daily_price  ────►│ Traditional BFP             │──► short scorecard
                    │ Technical AI (price only)   │──► (future short replay)
                    └─────────────────────────────┘

  dividend_event ┐
  fundamentals_* ├─► hold_features.extract()
  stock_code     ┘         │
                           ├─► Chen rules (deterministic) ──► hold scorecard
                           └─► Hold AI (Chen-framed Gemini) ──► narrative
```

Stock Detail layout (target):

1. Section **Traditional** — BFP + MA + short backtest (unchanged)
2. Section **Technical AI** — existing position call (unchanged)
3. Section **Hold / 存股** — Chen rules card + feature panel + Hold AI + hold scorecard
4. Dividends / chips remain adjacent data, not mixed into BFP

Comparison means **side-by-side panels and two different gradesheets**, not one
blended score.

---

## Chen methodology → computable dimensions

Public framework (books / interviews), turned into weighted checklist items:

| Dimension | Chen idea | Measurable rule (defaults, tunable) | Data |
|-----------|-----------|-------------------------------------|------|
| **Earn** | Year-after-year profit | Count of years with EPS > 0 over last 10 | `fundamentals_annual` |
| **Efficient** | Prefer efficient capital use | Avg ROE / stability; compare peers in `group` when enough peers exist | same |
| **Cheap** | Don’t overpay | Trailing PE vs band (e.g. financials ~10×) or vs own 5y history | EPS + `daily_price` |
| **Collect** | Stable cash return | Years with cash dividend; TTM cash yield (e.g. ≥ 5%) | `dividend_event` |
| **Liquid** | Can actually accumulate | Rough avg daily volume floor | `daily_price` |

Rule engine output (example):

- Per-dimension: `pass` | `fail` | `unknown` + short evidence string  
- `score` 0–100 over **known** dimensions only (unknowns shrink the denominator)  
- `suitability_rule`: `strong` / `ok` / `weak` / `avoid` from score bands  
- Explicit `coverage_gaps` (e.g. no annual EPS, TPEx dividend history thin)

Qualitative Chen language (“護城河”, “能傳”) stays **AI narrative only**, not hard
rules, until we have a deliberate labelling scheme.

---

## Hold AI (separate from technical AI)

Same operating principles as `features.py` / `gemini.py`:

1. **Code does arithmetic**; the model only judges.  
2. **Reuse** Gemini client, throttle, and daily quota accounting.  
3. **Do not reuse** `ai_analysis` rows, technical `SYSTEM_INSTRUCTION`, or
   `enter`/`exit`/`size` vocabulary.

Suggested verdict shape:

| Field | Values |
|-------|--------|
| `suitability` | `strong` / `ok` / `weak` / `avoid` |
| `confidence` | `high` / `medium` / `low` |
| `headline` | one sentence |
| `reasons` | 2–4 strings, each citing a supplied number |
| `risks` | 1–3 strings |
| optional `agrees_with_rules` | bool — vs Chen rule suitability |

Prompt rules:

- Use only supplied hold features + Chen rule summary.  
- If `coverage_gaps` is non-empty, lower confidence; never invent EPS/ROE.  
- Frame as long-horizon dividend holding, not day trading.  
- Own `PROMPT_VERSION` (e.g. `hold-v1`) in the cache key.

---

## Data model

### New tables

**`fundamentals_annual`**

| Column | Notes |
|--------|--------|
| `sid`, `year` | PK |
| `eps`, `roe` | Core Chen inputs |
| `net_income`, `equity` | Optional; ROE audit / growth |
| `source` | e.g. `finmind` / `mops` / `manual` |
| `updated_at` | |

**`fundamentals_fetch_log`**

Mirror `dividend_fetch_log` / `fetch_log`: source + bucket + row_count + fetched_at.

**`ai_hold_analysis`**

Mirror `ai_analysis` skeleton:

- Unique key `(sid, as_of, model, prompt_version, locale)`  
- Verdict columns for hold suitability (not action/size)  
- `features` JSONB = exact hold feature snapshot  
- `requested_by` + token/latency fields for quota and cost

Alembic: next revision after `0007_watchlist_groups` (e.g. `0008_chen_hold`).

### Existing reuse

- `dividend_event` — cash/stock, ex-dates  
- `daily_price` — close for yield, volume for liquid, PE numerator  
- `stock_code.group` — industry label for peer ROE later  

### Ingest

| Source | Role | MVP |
|--------|------|-----|
| TWSE TWT49U (existing) | Board-wide cash dividend history | **Warmup job** so yield/continuity are not lazy-only |
| TPEx | Recent window only | Mark coverage `recent`; do not pretend 10y continuity |
| Fundamentals provider (FinMind / MOPS / …) | Annual EPS/ROE | Schema + service interface first; token via env when wired |

Without annual fundamentals, Earn / Efficient / Cheap stay `unknown`; Collect /
Liquid still score from dividends and prices. UI must say so.

---

## APIs (proposed)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/stocks/{sid}/analysis/chen` | **None** — see below | Chen rules + hold features |
| POST | `/api/stocks/{sid}/analysis/ai-hold` | Signed-in | Hold AI generate / cache |
| GET | `/api/stocks/{sid}/analysis/hold-backtest` | Optional | Long-horizon hold scorecard |
| GET | `/api/analysis/ai/quota` | Signed-in | Prefer **shared** count of tech + hold paid rows |

**Shipped as unauthenticated and cache-only.** The snapshot reads a fixed
two-year price window and eleven years of dividends, which is wider than
`ANONYMOUS_MAX_MONTHS` — so an anonymous cap would have refused every
signed-out reader outright, and lazy fetching would have let one such reader
queue tens of exchange calls on the limiter the realtime poll shares. The route
therefore never calls the exchange, which removes the budget an anonymous
window exists to protect. The warmup job below is what makes that workable
rather than merely cheap.

Job: `dividend_board_warmup` — ensure last N TWSE years in `dividend_fetch_log`
(and TPEX live bucket), so board-level or first-open screens do not rely only on
per-stock lazy fetch.

---

## Hold scorecard (“平測”) vs short scorecard

Short engines must **not** be graded with the same 5/10/20-day hit rates as 存股.

| Short (existing) | Hold (new) |
|------------------|------------|
| Signal after next open | Buy at window start (or on “cheap” rule), hold to end |
| Hit rate / edge vs day baseline | Total return **including cash dividends** |
| Holding days of trades | Dividend contribution vs price contribution |
| — | Max drawdown; rough fill/stickiness of ex-div gaps if data allows |
| — | Optional: buckets by Chen score vs subsequent N-year outcomes |

Later: a watchlist “methodology arena” ranking the same sids under each engine.
Out of scope for the first milestone.

---

## Frontend

- New cards on **Stock Detail** only for hold (not every watchlist expand — paid AI).  
- `ChenHoldCard`: score, dimension chips, gaps.  
- `HoldAiVerdict`: button-gated like technical AI; shared quota copy.  
- `HoldBacktestCard`: long scorecard under the hold section.  
- i18n: keys in `zh-TW.ts` (source of truth) + `en.ts`; job labels via `serverText.ts`.  
- Section label e.g. `section.hold` — “存股分析” / “Hold analysis”.  
- Disclaimer: not investment advice; Chen framing is an encoding of a public
  method, not an endorsement.

---

## Implementation milestones

### M1 — Rules + dividend features + Hold AI — **done**

1. [x] Alembic `0008_chen_hold`: `fundamentals_annual`, `fundamentals_fetch_log`,
   `ai_hold_analysis`  
2. [x] `hold_features.py` from dividends + prices + empty/partial fundamentals  
3. [x] `chen_rules.py` deterministic score  
4. [x] `hold_gemini.py` + `hold_ai.py` (mirror `gemini.py` / `ai.py`)  
5. [x] GET `/analysis/chen`, POST `/analysis/ai-hold`  
6. [x] Stock Detail hold section + i18n (`section.hold`, `hold.*`, `holdAi.*`)  
7. [x] `test_hold_features.py`, `test_chen_rules.py`, `test_hold_ai.py`  
8. [x] `dividend_board_warmup` job — not on the original list, and load-bearing
   once `/analysis/chen` became cache-only: it is what fills the payout archive
   the checklist scores from  

Two things the build changed from the design above:

* **`/analysis/chen` is cache-only and unauthenticated**, not "optional auth
  with anon window caps". See the APIs section for why the caps could not work.
* **Streak continuity counts the current year once it has paid.** The design
  said to exclude the current year, which is right while a payment is pending
  and wrong the moment it goes ex — excluding it would make every streak lag
  its own evidence by a year.
* **`years_observed` had to be added to the dividend features.** The design
  leaned on `coverage` to decide whether an absent payout year was evidence,
  but `coverage` describes the exchange, not this table: on a fresh database
  TWSE is still `history` while only ~5 years have been pulled. Without the
  distinction, every dividend payer failed Collect on a streak that was really
  a fetch window — the exact mistake `unknown` exists to prevent. A streak
  bounded by the oldest observed year is now `unknown`, not `fail`.

**Done when:** a TWSE name with dividend history shows a Chen score and can
request a Hold AI verdict without touching technical AI cache keys. ✓

### M2 — Fundamentals ingest

1. Provider client behind env (e.g. `FINMIND_TOKEN`)  
2. Warm/backfill annual EPS/ROE  
3. Bump hold `PROMPT_VERSION` when feature JSON gains real Earn/Cheap fields  

**Done when:** Earn / Efficient / Cheap leave `unknown` for covered names.

### M3 — Hold backtest + comparison UX

1. `hold_backtest.py` + GET endpoint  
2. Hold scorecard card beside short backtest  
3. Short README note: four engines, two gradesheets  

**Done when:** one stock page shows traditional short grade + hold long grade.

### M4 — Optional

- Board screener on yield / score  
- Technical AI replay (closes existing README gap)  
- Peer ROE ranks, PEG-style bands, qualitative tags  

---

## Explicit non-goals (for M1–M3)

- Merging Hold AI into `ai_analysis` or the technical prompt  
- Claiming TPEx has 10-year dividend archives with current sources  
- Automating “護城河 / 能傳” as pass/fail without a data product  
- Replacing Best Four Point or implying 存股 beats short signals on the short scorecard  

---

## Feasibility summary

| Piece | Feasibility | Main risk |
|-------|-------------|-----------|
| Chen rules on dividends + price | High | Warmup + rate limits |
| Parallel Hold AI | High | Prompt/version discipline |
| Fundamentals store + later ingest | Medium | Provider stability / licensing |
| Hold backtest with dividends | Medium | Correct ex-div accounting |
| Full qualitative Chen clone | Low | Leave to narrative AI |

Recommended ship order: **M1 → M2 → M3**. M1 already delivers “more methods to
compare” on the page; M3 makes 平測 numeric.

---

## File touch list (expected)

| Area | Paths |
|------|--------|
| Migration | `server/alembic/versions/…_0008_chen_hold.py` |
| Models / schemas | `server/app/models.py`, `schemas.py`, `config.py` |
| Features / rules / AI | `server/app/services/analysis/hold_features.py`, `chen_rules.py`, `hold_gemini.py`, `hold_ai.py`, `hold_backtest.py` |
| Fundamentals / job | `server/app/services/fundamentals.py`, `jobs/handlers.py`, `jobs/registry.py` |
| API | `server/app/routers/analysis.py` |
| UI | `frontend/src/components/ChenHoldCard.tsx`, `HoldAiVerdict.tsx`, `HoldBacktestCard.tsx`, `pages/StockDetail.tsx` |
| Client / i18n | `frontend/src/api/types.ts`, `client.ts`, `i18n/locales/*`, `i18n/serverText.ts` |
| Docs | this file; short README pointer when M1 ships |

---

## Decisions

1. **AI daily quota: shared.** One Gemini budget per account per day, counted
   across `ai_analysis` and `ai_hold_analysis` by `ai.quota_status`. Two
   counters would have meant that shipping this lane silently doubled what
   every existing account could spend, without anyone deciding to raise the
   limit. Both cards read the same `['ai-quota']` key, so the number on screen
   moves whichever button is pressed.
2. **Fundamentals provider: still open.** `services/fundamentals.py` has the
   read path, the upsert and the fetch stamp; a client is a function producing
   `Row`s and nothing above it needs to change. FinMind vs MOPS vs a
   hand-maintained CSV is a licensing and reliability call, not a code one.
3. **Chen thresholds: named constants at the top of `chen_rules.py`,** as
   planned. Shipped defaults: EPS positive in all but one of ≥ 5 years, average
   ROE ≥ 10% with stdev ≤ 8 over ≥ 3 years, trailing PE ≤ 10 for financials and
   ≤ 15 otherwise, an 8-year payout streak or a 5-year one at ≥ 5% yield,
   500 000 shares/day with under 10% no-trade sessions. Weights 25/20/20/25/10.
   None of these has been measured against outcomes yet — that is what M3 is
   for.

---

## Reference (product research snapshot)

Chen-style public heuristics used to design dimensions (not copied as licensed
content): multi-year EPS > 0, ROE for efficiency among peers, PE for cheapness,
stable dividends / often ~5%+ yield talk for financials, buy quality on
weakness, reinvest dividends, “高築牆、廣積糧、緩稱王”.

Existing board philosophy (README): three analyses that can be compared — this
plan extends that to a fourth/fifth lane with a **different** gradesheet.
