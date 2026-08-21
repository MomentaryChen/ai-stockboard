/**
 * 當日開盤情報, from whichever source can answer for the selected day.
 *
 * Three sources describe the same thing and none of them covers every case, so
 * they are normalised to one shape here rather than in the components:
 *
 *   realtime quote   the only source for *today* while the session runs, and
 *                    for the hours after it before TWSE publishes the report.
 *                    Signed-in only, and it carries `y` (yesterday's close),
 *                    which is what makes the gap computable.
 *   /api/market/open settled daily bars for any past session -- and for today
 *                    once the report lands. The only source with a previous
 *                    close for a *watchlist* stock on an arbitrary date.
 *   chart rows       the history the dashboard already loaded. Covers today
 *                    after publication for a signed-out visitor, who gets no
 *                    quote, without spending a second request.
 *
 * The whole point of the board is the pair (gap, fromOpen): a day that gaps up
 * 1% and fades to flat closes in the same place as a day that opened flat and
 * went nowhere, and only these two numbers tell them apart.
 */

import type { MoveDirection, OpenSnapshot, RealtimeQuote } from '../api/types'

export interface OpenView {
  sid: string
  name: string
  /** The session this describes, 'YYYY-MM-DD'. */
  date: string
  /** `last` is a live tick rather than a settled close. */
  intraday: boolean
  open: number | null
  prevClose: number | null
  gap: number | null
  gapPct: number | null
  gapDirection: MoveDirection
  high: number | null
  low: number | null
  /** 現價 while intraday, otherwise the day's close. */
  last: number | null
  change: number | null
  changePct: number | null
  fromOpen: number | null
  fromOpenPct: number | null
  driftDirection: MoveDirection
  capacity: number | null
  turnover: number | null
}

/** Mirrors `market_open.FLAT_PCT` on the server, so a move classed as flat in a
 *  settled snapshot is still flat when the same day is read from a live quote. */
const FLAT_PCT = 0.05

function pct(delta: number | null, base: number | null): number | null {
  if (delta === null || !base) return null
  return Math.round((delta / base) * 100 * 100) / 100
}

function direction(value: number | null): MoveDirection {
  if (value === null) return 'flat'
  if (value > FLAT_PCT) return 'up'
  if (value < -FLAT_PCT) return 'down'
  return 'flat'
}

function delta(a: number | null | undefined, b: number | null | undefined): number | null {
  if (a === null || a === undefined || b === null || b === undefined) return null
  return Math.round((a - b) * 10000) / 10000
}

/** The server's shape, renamed. No arithmetic -- it already did it. */
export function fromSnapshot(snapshot: OpenSnapshot): OpenView {
  return {
    sid: snapshot.sid,
    name: snapshot.name,
    date: snapshot.date,
    intraday: snapshot.intraday,
    open: snapshot.open,
    prevClose: snapshot.prev_close,
    gap: snapshot.gap,
    gapPct: snapshot.gap_percent,
    gapDirection: snapshot.gap_direction,
    high: snapshot.high,
    low: snapshot.low,
    last: snapshot.last,
    change: snapshot.change,
    changePct: snapshot.change_percent,
    fromOpen: snapshot.from_open,
    fromOpenPct: snapshot.from_open_percent,
    driftDirection: snapshot.drift_direction,
    capacity: snapshot.capacity,
    turnover: snapshot.turnover,
  }
}

/** 現價: the snapshot print, or the inside of the book when this 5-second
 *  window had no trade -- see `realtime._session_price` for why. */
export function lastPrice(quote: RealtimeQuote): number | null {
  return quote.latest_trade_price ?? quote.best_bid_price[0] ?? quote.best_ask_price[0] ?? null
}

/**
 * Today, live.
 *
 * `change` and `change_percent` come off the quote rather than being recomputed:
 * the realtime service already derived them from the same `y`, and duplicating
 * the arithmetic here is how the header and the board start disagreeing in the
 * last decimal place.
 *
 * MIS keeps serving the last tick long after 13:30, which is a feature here --
 * an evening visitor sees the session that just finished, correctly marked as
 * intraday until the daily bar takes over.
 */
export function fromQuote(quote: RealtimeQuote, date: string): OpenView {
  const last = lastPrice(quote)
  const gap = delta(quote.open, quote.yesterday_close)
  const fromOpen = delta(last, quote.open)
  const gapPct = pct(gap, quote.yesterday_close)
  const fromOpenPct = pct(fromOpen, quote.open)

  return {
    sid: quote.code,
    name: quote.name,
    date,
    intraday: true,
    open: quote.open,
    prevClose: quote.yesterday_close,
    gap,
    gapPct,
    gapDirection: direction(gapPct),
    high: quote.high,
    low: quote.low,
    last,
    change: quote.change,
    changePct: quote.change_percent,
    fromOpen,
    fromOpenPct,
    driftDirection: direction(fromOpenPct),
    // MIS counts 成交股數 like the daily bar does, so the same formatter applies.
    capacity:
      quote.accumulate_trade_volume === null ? null : quote.accumulate_trade_volume * 1000,
    // Not quoted. The turnover tile falls back to the daily bar, or shows '--'.
    turnover: null,
  }
}

/** Just enough of a chart row to derive an open view from history. */
export interface HistoryBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  change: number | null
  capacity: number | null
  turnover: number | null
}

/**
 * The selected day out of the history the chart already holds.
 *
 * The fallback that costs nothing: a signed-out visitor reading today's board
 * after the report lands has no quote, and the open endpoint may not have
 * caught up, but these rows are on the page regardless.
 *
 * The previous close comes from the preceding row, or from this row's own
 * `change` when the window starts here -- the same order of preference the
 * server applies, for the same reason.
 */
export function fromHistory(bars: HistoryBar[], date: string, name: string, sid: string): OpenView | null {
  const index = bars.findIndex((bar) => bar.date === date)
  if (index < 0) return null

  const bar = bars[index]
  const previous = index > 0 ? bars[index - 1] : null
  const prevClose =
    previous?.close ?? (bar.close !== null && bar.change !== null ? bar.close - bar.change : null)

  const gap = delta(bar.open, prevClose)
  const change = delta(bar.close, prevClose)
  const fromOpen = delta(bar.close, bar.open)
  const gapPct = pct(gap, prevClose)
  const fromOpenPct = pct(fromOpen, bar.open)

  return {
    sid,
    name,
    date,
    intraday: false,
    open: bar.open,
    prevClose,
    gap,
    gapPct,
    gapDirection: direction(gapPct),
    high: bar.high,
    low: bar.low,
    last: bar.close,
    change,
    changePct: pct(change, prevClose),
    fromOpen,
    fromOpenPct,
    driftDirection: direction(fromOpenPct),
    capacity: bar.capacity,
    turnover: bar.turnover,
  }
}

/** Message key for 開高走低 and its eight siblings. */
export function patternKey(view: OpenView) {
  return `open.pattern.${view.gapDirection}.${view.driftDirection}` as const
}
