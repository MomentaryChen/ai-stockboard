/**
 * 自選股, from whichever store applies right now.
 *
 * Signed out it is localStorage; signed in it is the database. Callers see one
 * `string[]` plus add/remove either way, which is what lets RealtimeBoard keep
 * its 10-second realtime poll exactly as it was.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { WatchlistResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { MAX_WATCHLIST, loadWatchlist, saveWatchlist } from '../watchlistStorage'

// Hoisted so the "not known yet" result keeps a stable identity across renders.
const EMPTY: string[] = []

export function useWatchlist() {
  const { status } = useAuth()
  const authenticated = status === 'authenticated'
  const queryClient = useQueryClient()

  const [local, setLocal] = useState<string[]>(loadWatchlist)

  useEffect(() => {
    // Re-read on every drop back to anonymous. Signing in merges the local copy
    // into the account and clears it, but this state still holds the pre-sign-in
    // list -- persisting *that* on sign-out would resurrect the copy, and the
    // next account signed in on this browser would inherit someone else's stocks.
    if (!authenticated) setLocal(loadWatchlist())
  }, [authenticated])

  const remote = useQuery({
    queryKey: ['watchlist'],
    queryFn: api.getWatchlist,
    enabled: authenticated,
    staleTime: Infinity,
  })

  const save = useMutation({
    mutationFn: api.putWatchlist,
    // Optimistic, so adding a stock feels as instant as the localStorage path.
    onMutate: async (sids: string[]) => {
      await queryClient.cancelQueries({ queryKey: ['watchlist'] })
      const previous = queryClient.getQueryData<WatchlistResponse>(['watchlist'])
      queryClient.setQueryData<WatchlistResponse>(['watchlist'], {
        count: sids.length,
        sids,
      })
      return { previous }
    },
    onError: (_error, _sids, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['watchlist'], context.previous)
      }
    },
    // The endpoint returns the resulting list, so no follow-up GET is needed.
    onSuccess: (result) => queryClient.setQueryData(['watchlist'], result),
  })

  const sids = useMemo(() => {
    // Until /api/auth/me settles we do not know which store to read. Falling
    // back to `local` here would hand back DEFAULT_WATCHLIST for a signed-in
    // user -- their sign-in cleared the local copy -- which both flashes the
    // wrong three stocks and spends a TWSE request (3 per 5 seconds) quoting
    // them before the real list arrives.
    if (status === 'loading') return EMPTY
    return authenticated ? (remote.data?.sids ?? []) : local
  }, [status, authenticated, remote.data, local])

  const commit = useCallback(
    (next: string[]) => {
      // Writing before we know who is asking would put a signed-in user's edit
      // into localStorage, where the next sign-in would merge it back in.
      if (status === 'loading') return
      if (authenticated) {
        save.mutate(next)
      } else {
        setLocal(next)
        saveWatchlist(next)
      }
    },
    [status, authenticated, save],
  )

  const add = useCallback(
    (code: string) => {
      if (sids.includes(code) || sids.length >= MAX_WATCHLIST) return
      commit([...sids, code])
    },
    [commit, sids],
  )

  const remove = useCallback(
    (code: string) => commit(sids.filter((sid) => sid !== code)),
    [commit, sids],
  )

  return {
    sids,
    add,
    remove,
    isFull: sids.length >= MAX_WATCHLIST,
    // 'loading' covers the moment between a page load and /api/auth/me
    // resolving, when we do not yet know which store to read.
    isLoading: status === 'loading' || (authenticated && remote.isPending),
    error: (save.error ?? remote.error) as Error | null,
  }
}
