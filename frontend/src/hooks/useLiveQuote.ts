/**
 * The headline price for one sid: the live tick while it is worth having, the
 * last daily close otherwise.
 *
 * 大盤 and 個股 need exactly the same decision -- MIS keeps serving the last
 * tick long after the close, so "is there a quote" is not the same question as
 * "is the market open" -- and it was copied between the two pages. Formatting
 * stays with the callers, because an index reads as 44,933.74 and a stock as
 * 512.00.
 *
 * Quotes are signed-in only. Anonymous visitors keep the whole page -- charts,
 * moving averages, 四大買賣點 -- and simply read the last close instead of the
 * live tick, which is the fallback this hook already had for weekends.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { RealtimeQuote } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { isMarketOpen } from '../utils/market'

export const POLL_MS = 10_000

/** Just enough of PriceChart's ChartRow to fall back on. */
export interface ClosingBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  change: number | null
}

export interface LiveQuote {
  quote: RealtimeQuote | undefined
  /** Taiwan regular session, right now. */
  marketOpen: boolean
  /** No account, so no quote: show the close and invite them to sign in. */
  locked: boolean
  /** What the numbers on screen actually are. Drives the 盤中 / 收盤 badge --
   *  a locked page during market hours is still showing a close. */
  sessionLabel: '盤中' | '收盤'
  /** Whether the numbers below came from the quote rather than the daily bar. */
  intraday: boolean
  price: number | null
  change: number | null
  changePct: number | null
  open: number | null
  high: number | null
  low: number | null
  /** '13:24:58' while intraday, otherwise the last close's date. */
  stamp: string
  live: boolean
  setLive: React.Dispatch<React.SetStateAction<boolean>>
  isFetching: boolean
}

export function useLiveQuote(sid: string, lastClose: ClosingBar | undefined): LiveQuote {
  const { status } = useAuth()
  const authenticated = status === 'authenticated'
  // 'loading' is not 'anonymous': on a hard refresh the session is still being
  // restored, and prompting there would flash a sign-in box at somebody who is
  // already signed in.
  const locked = status === 'anonymous'

  // Polling off-session would only re-fetch the same last tick, so start live
  // only while the exchange is open. The user can still switch it on.
  const [live, setLive] = useState(isMarketOpen)

  // Daily bars and the rule analysis are close-of-day data behind a PostgreSQL
  // cache -- re-fetching those intraday returns the same row, so only the quote
  // polls.
  const realtime = useQuery({
    queryKey: ['realtime', [sid]],
    queryFn: () => api.getRealtime([sid]),
    enabled: authenticated,
    refetchInterval: live ? POLL_MS : false,
    staleTime: 0,
  })

  const quote = realtime.data?.quotes.find((q) => q.code === sid)
  const marketOpen = isMarketOpen()

  // Show the live number while the session runs, and also in the gap after
  // 13:30 before TWSE publishes the day's report -- until then `lastClose` is
  // still yesterday.
  const quoteDay = quote?.time?.slice(0, 10) ?? null
  const intraday =
    quote?.latest_trade_price != null &&
    (marketOpen || (quoteDay !== null && quoteDay > (lastClose?.date ?? '')))

  const changePct =
    intraday && quote!.change_percent != null
      ? quote!.change_percent
      : lastClose && lastClose.change !== null && lastClose.close - lastClose.change !== 0
        ? (lastClose.change / (lastClose.close - lastClose.change)) * 100
        : null

  return {
    quote,
    marketOpen,
    locked,
    sessionLabel: intraday ? '盤中' : '收盤',
    intraday,
    price: intraday ? quote!.latest_trade_price : (lastClose?.close ?? null),
    change: intraday ? quote!.change : (lastClose?.change ?? null),
    changePct,
    open: intraday ? quote!.open : (lastClose?.open ?? null),
    high: intraday ? quote!.high : (lastClose?.high ?? null),
    low: intraday ? quote!.low : (lastClose?.low ?? null),
    stamp: intraday ? quote!.time.slice(11) : (lastClose?.date ?? '--'),
    live,
    setLive,
    isFetching: realtime.isFetching,
  }
}
