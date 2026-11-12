import { useMemo, useState } from 'react'
import { Bot, History, Search } from 'lucide-react'
import { useAsync } from '@/lib/hooks'
import { conversationsApi, stripRoute } from '@/lib/conversations'
import { formatDate, pluralise, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import type { ChatTurn } from '@/lib/types'
import { EmptyState, ErrorBanner, FieldSelect, Pill } from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

const WINDOWS = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 0, label: 'All time' },
]

interface Thread {
  waId: string
  name: string
  lastAt: string
  turns: ChatTurn[]
}

/**
 * Chat history is the record of everything the bot and agents have said: a read
 * only view for looking back, with a day window and a search box. Grouped by
 * customer so a long tail of turns reads as conversations.
 */
export function ChatHistoryPanel({ tenantId }: { tenantId: string }) {
  const [days, setDays] = useState(30)
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  const state = useAsync(
    (signal) =>
      conversationsApi.history(tenantId, { days: days || undefined, search: search || undefined, limit: 500 }, signal),
    [tenantId, days, search],
  )

  const threads = useMemo<Thread[]>(() => {
    const map = new Map<string, Thread>()
    for (const row of state.data ?? []) {
      const existing = map.get(row.wa_id)
      if (existing) {
        existing.turns.push(row)
        if (row.created_at > existing.lastAt) existing.lastAt = row.created_at
      } else {
        map.set(row.wa_id, {
          waId: row.wa_id,
          name: row.sender_name || row.wa_id,
          lastAt: row.created_at,
          turns: [row],
        })
      }
    }
    return [...map.values()].sort((a, b) => b.lastAt.localeCompare(a.lastAt))
  }, [state.data])

  const active = threads.find((t) => t.waId === selected) ?? null
  const totalTurns = (state.data ?? []).length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Past chats"
        description="Every recorded exchange with this tenant's customers — what the bot answered and what agents said."
        meta={
          state.data && (
            <>
              <Pill tone="ok">{pluralise(totalTurns, 'turn')}</Pill>
              <Pill>{pluralise(threads.length, 'conversation')}</Pill>
            </>
          )
        }
        actions={
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              setSearch(draft.trim())
            }}
          >
            <label className="flex items-center gap-2 rounded-lg border border-line bg-inputbg px-3 py-2">
              <Search className="h-4 w-4 shrink-0 text-txfaint" />
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Search messages…"
                className="w-56 bg-transparent text-sm text-tx placeholder:text-txfaint focus:outline-none"
              />
            </label>
            <div className="w-40">
              <FieldSelect value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time window">
                {WINDOWS.map((w) => (
                  <option key={w.value} value={w.value}>
                    {w.label}
                  </option>
                ))}
              </FieldSelect>
            </div>
          </form>
        }
      />

      {/*
        Two panes, always side by side. Both grid tracks are `minmax(0, …)` so
        a long message can never stretch the transcript column past the page,
        and the single `minmax(0, 1fr)` row keeps both cards exactly as tall as
        the pane — the lists scroll inside them, the page does not.
      */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,13rem)_minmax(0,1fr)] grid-rows-[minmax(0,1fr)] gap-4 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)] lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border border-line bg-card">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3">
            <span className="text-txmuted">
              <History className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-tx">Conversations</h2>
          </div>
          {state.loading && !threads.length ? (
            <LoadingBlock label="Loading history…" />
          ) : state.error ? (
            <div className="p-4">
              <ErrorBanner title="Could not load" message={state.error} />
            </div>
          ) : threads.length === 0 ? (
            <EmptyState className="flex-1" title="No history" description="Nothing matches this window." />
          ) : (
            <ul className="min-h-0 flex-1 divide-y divide-linesoft overflow-y-auto overflow-x-hidden">
              {threads.map((thread) => (
                <li key={thread.waId}>
                  <button
                    type="button"
                    onClick={() => setSelected(thread.waId)}
                    className={cn(
                      'w-full px-4 py-3 text-left transition',
                      thread.waId === selected ? 'bg-accsoft' : 'hover:bg-paper',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-tx">
                        {thread.name}
                      </span>
                      <span className="shrink-0 text-[11px] text-txfaint">{relativeTime(thread.lastAt)}</span>
                    </div>
                    <p className="mt-1 text-[11px] text-txmuted">
                      {pluralise(thread.turns.length, 'turn')} · {formatDate(thread.lastAt)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border border-line bg-card">
          {!active ? (
            <EmptyState
              className="flex-1"
              title="Pick a conversation"
              description="Select a customer to read the full transcript."
            />
          ) : (
            <>
              <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3">
                <span className="text-txmuted">
                  <Bot className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-tx">{active.name}</h2>
                  <p className="text-xs text-txmuted">
                    {active.waId} · {pluralise(active.turns.length, 'turn')} in this window
                  </p>
                </div>
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overflow-x-hidden break-words px-4 py-4">
                {[...active.turns]
                  .sort((a, b) => a.created_at.localeCompare(b.created_at))
                  .map((turn) => (
                    <div key={turn.id} className="space-y-1.5">
                      {stripRoute(turn.message) && (
                        <div className="flex justify-start">
                          <div className="min-w-0 max-w-[80%]">
                            <div className="rounded-panel rounded-bl-sm bg-bubblein px-3.5 py-2.5 text-sm text-tx">
                              {stripRoute(turn.message)}
                            </div>
                            <p className="mt-1 text-[11px] text-txmuted">{formatDate(turn.created_at)}</p>
                          </div>
                        </div>
                      )}
                      {turn.response && (
                        <div className="flex justify-end">
                          <div className="min-w-0 max-w-[80%]">
                            <div
                              className={cn(
                                'rounded-panel rounded-br-sm px-3.5 py-2.5 text-sm',
                                turn.route === 'agent'
                                  ? 'border border-dashed border-attline bg-card text-tx'
                                  : 'bg-acc text-onacc',
                              )}
                            >
                              {turn.response}
                            </div>
                            <p className="mt-1 text-right text-[11px] text-txmuted">
                              {turn.route === 'agent' ? 'Team reply' : `Bot · ${turn.route}`}
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
