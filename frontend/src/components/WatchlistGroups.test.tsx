/**
 * What the group bar shows while a stock is in flight.
 *
 * These assertions are the feature: before the shared drag context, a pickup
 * that started on the board was invisible to this component, so every chip
 * looked identical whether or not it would accept the drop -- and the chip for
 * the stock's *current* group accepted a drop that did nothing at all. The
 * classNames below are what a user reads to decide where to let go.
 */

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import WatchlistGroups from './WatchlistGroups'
import type { WatchlistGroup } from '../api/types'
import { WatchlistDragProvider, useWatchlistDrag } from '../hooks/watchlistDrag'
import { I18nProvider } from '../i18n'
import { SID_DRAG_TYPE } from '../utils/watchlistGroups'

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

const GROUPS: WatchlistGroup[] = [
  { id: 1, name: '存股' },
  { id: 2, name: '短線' },
] as WatchlistGroup[]

/** 2330 is filed under group 1; 1101 is ungrouped. */
const GROUP_BY_SID: Record<string, number> = { '2330': 1 }
const SIDS = ['2330', '1101']

/** Reaches into the same provider the bar reads, so the test can start a drag
 *  the way a board row does -- from outside this component. */
let begin: (code: string, from: number | null) => void = () => {}
let end: () => void = () => {}

function DragHandle() {
  const drag = useWatchlistDrag()
  begin = drag.begin
  end = drag.end
  return null
}

function mount(onAssign?: (code: string, groupId: number | null) => void) {
  act(() => {
    root.render(
      <I18nProvider>
        <WatchlistDragProvider>
          <DragHandle />
          <WatchlistGroups
            groups={GROUPS}
            groupBySid={GROUP_BY_SID}
            sids={SIDS}
            filter={{ kind: 'all' }}
            onFilter={() => {}}
            onCreate={async () => {}}
            onRename={async () => {}}
            onDelete={async () => {}}
            onAssign={onAssign}
          />
        </WatchlistDragProvider>
      </I18nProvider>,
    )
  })
}

/**
 * The two pinned chips, by position rather than by label: their names come from
 * the catalogue, and the test locale is not the one a reader of this file would
 * guess. The user's own group names are props, so those are matched by name.
 */
function fixedChip(index: 0 | 1): HTMLElement {
  const chips = container.querySelectorAll('.group-bar-fixed .group-chip')
  const found = chips[index]
  if (!found) throw new Error(`no fixed chip at ${index}`)
  return found as HTMLElement
}

const allChip = () => fixedChip(0)
const ungroupedChip = () => fixedChip(1)

/** A user group's chip, walked up to the wrapper that carries the drop state. */
function chip(name: string): HTMLElement {
  const found = [...container.querySelectorAll('.group-bar-scroll .group-chip-name')].find(
    (node) => node.textContent === name,
  )
  if (!found) throw new Error(`no chip named ${name}`)
  const button = found.closest('.group-chip') as HTMLElement
  const wrap = button.closest('.group-chip-wrap')
  return (wrap ?? button) as HTMLElement
}

describe('WatchlistGroups drop affordance', () => {
  it('advertises nothing while no drag is running', () => {
    mount(() => {})
    expect(container.querySelectorAll('.drop-target')).toHaveLength(0)
    expect(container.querySelectorAll('.drop-current')).toHaveLength(0)
    expect(container.querySelector('.group-bar')?.className).not.toContain('dragging')
  })

  it('lights up every eligible bucket when a drag starts elsewhere', () => {
    mount(() => {})
    act(() => begin('2330', 1))

    // 2330 lives in 存股, so that one says so instead of offering a no-op drop.
    expect(chip('存股').className).toContain('drop-current')
    expect(chip('存股').className).not.toContain('drop-target')

    // The other two buckets are real destinations.
    expect(chip('短線').className).toContain('drop-target')
    expect(ungroupedChip().className).toContain('drop-target')

    // 全部 is a filter, not a folder.
    expect(allChip().className).toContain('drop-inert')
    expect(allChip().className).not.toContain('drop-target')
  })

  it('stops advertising once the drag ends', () => {
    mount(() => {})
    act(() => begin('2330', 1))
    expect(container.querySelectorAll('.drop-target').length).toBeGreaterThan(0)

    act(() => end())
    expect(container.querySelectorAll('.drop-target')).toHaveLength(0)
    expect(container.querySelectorAll('.drop-current')).toHaveLength(0)
  })

  it('stays inert on the read-only variant, which has no onAssign', () => {
    mount(undefined)
    act(() => begin('2330', 1))
    expect(container.querySelectorAll('.drop-target')).toHaveLength(0)
    expect(container.querySelectorAll('.drop-current')).toHaveLength(0)
  })
})

/** A drop carrying the private flavour, the way a grip starts one. */
function dropOn(element: HTMLElement, code: string) {
  const dataTransfer = new DataTransfer()
  dataTransfer.setData(SID_DRAG_TYPE, code)
  dataTransfer.setData('text/plain', code)
  const event = new DragEvent('drop', { bubbles: true, cancelable: true })
  // happy-dom builds DragEvent without honouring a dataTransfer in the init
  // dict, and the property is readonly on the prototype.
  Object.defineProperty(event, 'dataTransfer', { value: dataTransfer })
  act(() => {
    element.dispatchEvent(event)
  })
}

describe('WatchlistGroups drop', () => {
  it('files the stock into the group it was dropped on', () => {
    const calls: Array<[string, number | null]> = []
    mount((code, groupId) => calls.push([code, groupId]))
    act(() => begin('2330', 1))

    dropOn(chip('短線'), '2330')
    expect(calls).toEqual([['2330', 2]])
  })

  it('says nothing to the server when the stock is dropped where it already is', () => {
    const calls: Array<[string, number | null]> = []
    mount((code, groupId) => calls.push([code, groupId]))
    act(() => begin('2330', 1))

    dropOn(chip('存股'), '2330')
    expect(calls).toEqual([])
  })

  it('takes a stock out of a group by dropping it on ungrouped', () => {
    const calls: Array<[string, number | null]> = []
    mount((code, groupId) => calls.push([code, groupId]))
    act(() => begin('2330', 1))

    dropOn(ungroupedChip(), '2330')
    expect(calls).toEqual([['2330', null]])
  })
})
