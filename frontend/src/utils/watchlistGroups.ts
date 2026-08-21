/**
 * Where a watchlist stock sits, and how it gets moved somewhere else.
 *
 * Sections and the drag payload live together because they are two halves of
 * one view rule: a section is the bucket a stock is in, and the drag is the
 * only cheap way to put it in another. The board, the card grid and the chip
 * row all read this, so "which bucket is this?" is answered identically in
 * three places instead of three times.
 */

import type { DragEvent } from 'react'

import type { WatchlistGroup } from '../api/types'

/** null is the ungrouped bucket -- the same value `onAssign` already takes. */
export type GroupId = number | null

export interface GroupSection<T> {
  id: GroupId
  name: string
  items: T[]
}

/**
 * Split items into one section per group, ungrouped last.
 *
 * Empty groups keep their heading on purpose: a group you just created has
 * nothing in it, and a section that is not drawn is a section you cannot drop
 * onto. The same goes for the ungrouped bucket -- it is how a stock gets *out*
 * of a group without opening the row.
 */
export function groupSections<T extends { code: string }>(
  items: T[],
  groups: WatchlistGroup[],
  groupBySid: Record<string, number>,
  ungroupedLabel: string,
): GroupSection<T>[] {
  const sections: GroupSection<T>[] = groups.map((group) => ({
    id: group.id,
    name: group.name,
    items: [],
  }))
  const byId = new Map(sections.map((section) => [section.id, section]))
  const ungrouped: GroupSection<T> = { id: null, name: ungroupedLabel, items: [] }

  for (const item of items) {
    const assigned = groupBySid[item.code] ?? null
    // An id that no longer resolves -- the group was deleted in another tab and
    // this cache has not caught up -- reads as ungrouped. Falling through to
    // nothing would drop the stock off a board that claims to show everything.
    const section = assigned === null ? ungrouped : (byId.get(assigned) ?? ungrouped)
    section.items.push(item)
  }

  return [...sections, ungrouped]
}

/**
 * A private flavour, so a code dragged off the board is distinguishable from
 * arbitrary text dropped in from another window. `text/plain` rides along
 * because some browsers refuse to start a drag that carries no standard type.
 */
export const SID_DRAG_TYPE = 'application/x-ai-stockboard-sid'

export function startSidDrag(event: DragEvent, code: string, label?: string) {
  event.dataTransfer.setData(SID_DRAG_TYPE, code)
  event.dataTransfer.setData('text/plain', code)
  event.dataTransfer.effectAllowed = 'move'
  if (label !== undefined) setSidDragImage(event, label)
}

/**
 * Drag a small card that names the stock, not a snapshot of the source.
 *
 * The browser's default ghost is a picture of the element being dragged, which
 * for a board row is a full-width slab of table that covers the very chips the
 * drop is aimed at. A chip-sized ghost keeps the targets visible, and naming
 * the stock is what makes a drag that crossed half the screen still legible.
 */
function setSidDragImage(event: DragEvent, label: string) {
  // The PiP window is a second document; its nodes cannot be parented here.
  const doc = event.currentTarget.ownerDocument
  const ghost = doc.createElement('div')
  ghost.className = 'sid-drag-ghost'
  ghost.textContent = label
  // Offscreen rather than hidden: `setDragImage` will not rasterise a node
  // that is not laid out, and `display: none` is not laid out.
  ghost.style.position = 'fixed'
  ghost.style.top = '-1000px'
  ghost.style.left = '-1000px'
  doc.body.appendChild(ghost)
  event.dataTransfer.setDragImage(ghost, 12, 12)
  // The browser has taken its snapshot by the time the frame ends; keeping the
  // node any longer would leak one per drag.
  requestAnimationFrame(() => ghost.remove())
}

/** `dragover` cannot read the payload -- the browser withholds it until the
 *  drop -- so the hover state has to be decided from the type list alone. */
export function isSidDrag(event: DragEvent): boolean {
  return event.dataTransfer.types.includes(SID_DRAG_TYPE)
}

export function readSidDrag(event: DragEvent): string | null {
  return event.dataTransfer.getData(SID_DRAG_TYPE) || null
}
