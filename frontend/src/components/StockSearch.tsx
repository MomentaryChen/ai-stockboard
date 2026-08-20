import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { StockInfo } from '../api/types'

interface Props {
  onSelect: (stock: StockInfo) => void
  placeholder?: string
  autoClearOnSelect?: boolean
}

/** Debounced code/name lookup with a keyboard-navigable dropdown. */
export default function StockSearch({
  onSelect,
  placeholder = '輸入股票代碼或名稱，例如 2330 或 台積電',
  autoClearOnSelect = false,
}: Props) {
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  // Debounce so typing "台積電" is one request, not four.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(input.trim()), 300)
    return () => clearTimeout(timer)
  }, [input])

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const { data, isFetching } = useQuery({
    queryKey: ['search', query],
    queryFn: ({ signal }) => api.searchStocks(query, 20, signal),
    enabled: query.length > 0,
  })

  const results = data?.results ?? []

  function choose(stock: StockInfo) {
    onSelect(stock)
    setOpen(false)
    if (autoClearOnSelect) {
      setInput('')
      setQuery('')
    } else {
      setInput(`${stock.code} ${stock.name}`)
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlight((h) => (h + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlight((h) => (h - 1 + results.length) % results.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      choose(results[highlight] ?? results[0])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="search" ref={boxRef}>
      <input
        value={input}
        placeholder={placeholder}
        onChange={(e) => {
          setInput(e.target.value)
          setHighlight(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />

      {open && query.length > 0 && (
        <div className="search-results">
          {results.map((stock, index) => (
            <button
              key={stock.code}
              type="button"
              className={`search-item ${index === highlight ? 'highlighted' : ''}`}
              onMouseEnter={() => setHighlight(index)}
              onClick={() => choose(stock)}
            >
              <span className="code">{stock.code}</span>
              <span className="name">{stock.name}</span>
              <span className="meta">
                {stock.market} · {stock.group || stock.type}
              </span>
            </button>
          ))}

          {results.length === 0 && (
            <div className="search-empty">
              {isFetching ? '搜尋中…' : `找不到符合「${query}」的股票`}
            </div>
          )}

          {data && data.total > results.length && (
            <div className="search-empty">
              共 {data.total} 筆，僅顯示前 {results.length} 筆
            </div>
          )}
        </div>
      )}
    </div>
  )
}
