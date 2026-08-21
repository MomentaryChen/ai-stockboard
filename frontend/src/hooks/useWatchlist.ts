/**
 * 自選股, from whichever store applies right now.
 *
 * Signed out it is localStorage; signed in it is the database. Callers see one
 * `string[]` plus add/remove either way, which is what lets RealtimeBoard keep
 * its 10-second realtime poll exactly as it was. Groups exist only on the
 * signed-in path -- there is no anonymous editor for them, and the localStorage
 * merge on sign-in is still a sid union.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { WatchlistGroup, WatchlistResponse } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { MAX_WATCHLIST, loadWatchlist, saveWatchlist } from '../watchlistStorage'

const EMPTY_SIDS: string[] = []
const EMPTY_GROUPS: WatchlistGroup[] = []
const EMPTY_ASSIGN: Record<string, number> = {}

type SaveArgs = {
  sids: string[]
  groupBySid?: Record<string, number | null>
}

function overlayAssignments(
  sids: string[],
  current: Record<string, number> | undefined,
  overlay: Record<string, number | null> | undefined,
): Record<string, number> {
  const next: Record<string, number> = {}
  for (const sid of sids) {
    const assigned = current?.[sid]
    if (assigned !== undefined) next[sid] = assigned
  }
  if (!overlay) return next
  for (const [sid, groupId] of Object.entries(overlay)) {
    if (!sids.includes(sid)) continue
    if (groupId === null) delete next[sid]
    else next[sid] = groupId
  }
  return next
}

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

  const remember = useCallback(
    (result: WatchlistResponse) => {
      queryClient.setQueryData(['watchlist'], result)
    },
    [queryClient],
  )

  const save = useMutation({
    mutationFn: ({ sids, groupBySid }: SaveArgs) => api.putWatchlist(sids, groupBySid),
    // Optimistic, so adding a stock feels as instant as the localStorage path.
    onMutate: async ({ sids, groupBySid }) => {
      await queryClient.cancelQueries({ queryKey: ['watchlist'] })
      const previous = queryClient.getQueryData<WatchlistResponse>(['watchlist'])
      queryClient.setQueryData<WatchlistResponse>(['watchlist'], {
        count: sids.length,
        sids,
        groups: previous?.groups ?? EMPTY_GROUPS,
        group_by_sid: overlayAssignments(sids, previous?.group_by_sid, groupBySid),
      })
      return { previous }
    },
    onError: (_error, _args, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['watchlist'], context.previous)
      }
    },
    onSuccess: remember,
  })

  const createGroup = useMutation({
    mutationFn: api.createWatchlistGroup,
    onSuccess: remember,
  })

  const renameGroup = useMutation({
    mutationFn: ({ groupId, name }: { groupId: number; name: string }) =>
      api.renameWatchlistGroup(groupId, name),
    onSuccess: remember,
  })

  const removeGroup = useMutation({
    mutationFn: api.deleteWatchlistGroup,
    onMutate: async (groupId: number) => {
      await queryClient.cancelQueries({ queryKey: ['watchlist'] })
      const previous = queryClient.getQueryData<WatchlistResponse>(['watchlist'])
      if (previous) {
        const group_by_sid = { ...previous.group_by_sid }
        for (const [sid, assigned] of Object.entries(group_by_sid)) {
          if (assigned === groupId) delete group_by_sid[sid]
        }
        queryClient.setQueryData<WatchlistResponse>(['watchlist'], {
          ...previous,
          groups: previous.groups.filter((group) => group.id !== groupId),
          group_by_sid,
        })
      }
      return { previous }
    },
    onError: (_error, _groupId, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['watchlist'], context.previous)
      }
    },
    onSuccess: remember,
  })

  const sids = useMemo(() => {
    // Until /api/auth/me settles we do not know which store to read. Falling
    // back to `local` here would hand back DEFAULT_WATCHLIST for a signed-in
    // user -- their sign-in cleared the local copy -- which both flashes the
    // wrong three stocks and spends a TWSE request (3 per 5 seconds) quoting
    // them before the real list arrives.
    if (status === 'loading') return EMPTY_SIDS
    return authenticated ? (remote.data?.sids ?? EMPTY_SIDS) : local
  }, [status, authenticated, remote.data, local])

  const groups = authenticated ? (remote.data?.groups ?? EMPTY_GROUPS) : EMPTY_GROUPS
  const groupBySid = authenticated
    ? (remote.data?.group_by_sid ?? EMPTY_ASSIGN)
    : EMPTY_ASSIGN

  const commit = useCallback(
    (next: string[], groupBySidOverlay?: Record<string, number | null>) => {
      // Writing before we know who is asking would put a signed-in user's edit
      // into localStorage, where the next sign-in would merge it back in.
      if (status === 'loading') return
      if (authenticated) {
        save.mutate({ sids: next, groupBySid: groupBySidOverlay })
      } else {
        setLocal(next)
        saveWatchlist(next)
      }
    },
    [status, authenticated, save],
  )

  const add = useCallback(
    (code: string, groupId?: number | null) => {
      if (sids.includes(code) || sids.length >= MAX_WATCHLIST) return
      const overlay =
        groupId === undefined ? undefined : { [code]: groupId }
      commit([...sids, code], overlay)
    },
    [commit, sids],
  )

  const remove = useCallback(
    (code: string) => commit(sids.filter((sid) => sid !== code)),
    [commit, sids],
  )

  const assign = useCallback(
    (code: string, groupId: number | null) => {
      if (!sids.includes(code)) return
      commit(sids, { [code]: groupId })
    },
    [commit, sids],
  )

  const mutationError =
    save.error ??
    createGroup.error ??
    renameGroup.error ??
    removeGroup.error ??
    remote.error

  return {
    sids,
    groups,
    groupBySid,
    add,
    remove,
    assign,
    createGroup: (name: string) => createGroup.mutateAsync(name),
    renameGroup: (groupId: number, name: string) =>
      renameGroup.mutateAsync({ groupId, name }),
    deleteGroup: (groupId: number) => removeGroup.mutateAsync(groupId),
    isFull: sids.length >= MAX_WATCHLIST,
    // 'loading' covers the moment between a page load and /api/auth/me
    // resolving, when we do not yet know which store to read.
    isLoading: status === 'loading' || (authenticated && remote.isPending),
    error: mutationError as Error | null,
  }
}
