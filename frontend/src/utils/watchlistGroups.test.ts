import { describe, expect, it } from 'vitest'

import type { WatchlistGroup } from '../api/types'
import { groupSections } from './watchlistGroups'

const GROUPS: WatchlistGroup[] = [
  { id: 1, name: '存股', position: 0 },
  { id: 2, name: '短打', position: 1 },
]

const items = (...codes: string[]) => codes.map((code) => ({ code }))

describe('groupSections', () => {
  it('keeps the group order and puts ungrouped last', () => {
    const sections = groupSections(
      items('2330', '2317', '0050'),
      GROUPS,
      { '2330': 2, '0050': 1 },
      'Ungrouped',
    )
    expect(sections.map((section) => section.id)).toEqual([1, 2, null])
    expect(sections.map((section) => section.items.map((item) => item.code))).toEqual([
      ['0050'],
      ['2330'],
      ['2317'],
    ])
  })

  it('keeps an empty group, so it stays a drop target', () => {
    const sections = groupSections(items('2330'), GROUPS, { '2330': 1 }, 'Ungrouped')
    expect(sections).toHaveLength(3)
    expect(sections[1]).toMatchObject({ id: 2, items: [] })
  })

  it('shows a stock whose group is gone rather than dropping it', () => {
    // The group was deleted in another tab and this cache has not caught up.
    const sections = groupSections(items('2330'), GROUPS, { '2330': 99 }, 'Ungrouped')
    expect(sections.at(-1)?.items.map((item) => item.code)).toEqual(['2330'])
  })
})
