import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, CheckCheck, Paperclip, RotateCcw, Search, SendHorizonal, UserRound } from 'lucide-react'
import { usePermissions } from '@/lib/permissions'
import { useAction, useAsync } from '@/lib/hooks'
import { useToast } from '@/components/ui/toast'
import { conversationsApi, stripRoute } from '@/lib/conversations'
import { attachmentsApi, type Attachment } from '@/lib/attachments'
import { AttachmentChip } from '@/components/tenants/chat-attachment'
import { EmptyState, LoadingBlock } from '@/components/ui/feedback'
import { ErrorBanner, GhostButton, PrimaryButton } from '@/components/remake/ui'
import type { InboxItem } from '@/lib/types'

interface FailedSend {
  id: number
  text: string
  reason: string
}

function waitLabel(iso?: string | null): string | null {
  if (!iso) return null
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m waiting`
  const h = Math.floor(mins / 60)
  return mins % 60 ? `${h}h ${mins % 60}m waiting` : `${h}h waiting`
}

/**
 * Chats — the live inbox in the new shell.
 *
 * Same data and same actions as before (queue, thread, handover, assign,
 * resolve, attachments, 12s polling); the composition follows the
 * confirmed mock: waiting-for-human first, customer bubbles on paper,
 * bot replies in teal, human replies outlined. One behaviour is new and
 * deliberate: a reply the provider refuses stays in the thread, marked
 * failed with its reason and a Retry button, instead of vanishing into
 * a toast. (The server thread does not store failed sends, so they are
 * kept for this session only.)
 */
export function ChatsPanel({ tenantId }: { tenantId: string }) {
  const { canWriteTenant, isSuperAdmin } = usePermissions()
  const canWrite = canWriteTenant('view_inbox')
  const toast = useToast()
  const action = useAction()
  const fileRef = useRef<HTMLInputElement>(null)

  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState('')
  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [failed, setFailed] = useState<Record<string, FailedSend[]>>({})
  const failedSeq = useRef(0)

  const inbox = useAsync((s) => conversationsApi.inbox(tenantId, true, s), [tenantId])
  const agents = useAsync((s) => conversationsApi.agents(tenantId, s), [tenantId])
  const thread = useAsync(
    (s) =>
      selected
        ? conversationsApi.thread(tenantId, selected, 300, s)
        : Promise.resolve({ wa_id: '', messages: [] }),
    [selected, tenantId],
  )
  const agentNames = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of agents.data ?? []) map.set(a.agent_id, a.username)
    return map
  }, [agents.data])

  // The inbox breathes: new handovers appear without a manual refresh.
  useEffect(() => {
    const t = window.setInterval(() => void inbox.reload(), 12_000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId])

  const queue = useMemo(() => {
    const items = [...(inbox.data?.queue ?? [])]
    items.sort((a, b) => {
      const ah = a.handover_mode === 'human' ? 0 : 1
      const bh = b.handover_mode === 'human' ? 0 : 1
      if (ah !== bh) return ah - bh
      const at = new Date(a.last_message_at ?? 0).getTime()
      const bt = new Date(b.last_message_at ?? 0).getTime()
      return ah === 0 ? at - bt : bt - at
    })
    const q = search.trim().toLowerCase()
    if (!q) return items
    return items.filter(
      (i) =>
        i.wa_id.includes(q) ||
        (i.name ?? '').toLowerCase().includes(q) ||
        (i.last_message ?? '').toLowerCase().includes(q),
    )
  }, [inbox.data, search])

  const current: InboxItem | undefined = queue.find((i) => i.wa_id === selected)
  const failedHere = selected ? (failed[selected] ?? []) : []

  const markFailed = (waId: string, text: string, reason: string) => {
    failedSeq.current += 1
    const entry: FailedSend = { id: failedSeq.current, text, reason }
    setFailed((prev) => ({ ...prev, [waId]: [...(prev[waId] ?? []), entry] }))
  }

  const sendReply = async (waId: string, text: string, att: Attachment | null) => {
    try {
      const res = await conversationsApi.reply(tenantId, waId, text, att)
      if (!res.sent) {
        markFailed(waId, text, `Not delivered (provider status: ${res.status ?? 'unknown'}).`)
        return false
      }
      return true
    } catch (err) {
      markFailed(waId, text, err instanceof Error ? err.message : 'Send failed.')
      return false
    }
  }

  const handleSend = async () => {
    if (!selected || (!draft.trim() && !attachment)) return
    const text = draft.trim()
    const att = attachment
    const ok = await sendReply(selected, text, att)
    if (ok) {
      setDraft('')
      setAttachment(null)
      toast.push('Reply sent')
      void thread.reload()
      void inbox.reload()
    }
  }

  const retry = async (entry: FailedSend) => {
    if (!selected) return
    const ok = await sendReply(selected, entry.text, null)
    if (ok) {
      setFailed((prev) => ({
        ...prev,
        [selected]: (prev[selected] ?? []).filter((f) => f.id !== entry.id),
      }))
      toast.push('Reply sent')
      void thread.reload()
    }
  }

  const setMode = async (mode: 'bot' | 'human') => {
    if (!selected) return
    const res = await action.run(() => conversationsApi.setHandover(tenantId, selected, mode))
    if (res) {
      toast.push(mode === 'bot' ? 'Conversation handed back to the bot' : 'You are handling this chat')
      void inbox.reload()
    }
  }

  const resolve = async () => {
    if (!selected) return
    const res = await action.run(() => conversationsApi.resolve(tenantId, selected))
    if (res) {
      toast.push('Conversation resolved')
      void inbox.reload()
    }
  }

  const assign = async (agentId: string) => {
    if (!selected || !agentId) return
    const res = await action.run(() => conversationsApi.assign(tenantId, selected, agentId))
    if (res) toast.push('Conversation assigned')
  }

  const upload = async (file: File) => {
    if (!selected) return
    const res = await action.run(() => attachmentsApi.upload(tenantId, file, 'inbox'))
    if (res) setAttachment(res)
  }

  return (
    <div>
      <header className="mb-5">
        <h1 className="text-xl font-semibold text-tx">Chats</h1>
        <p className="mt-1 text-sm text-txmuted">
          Live WhatsApp conversations. Chats waiting for a human are first.
          {isSuperAdmin ? ' View-only — replies and handovers are disabled.' : ''}
        </p>
      </header>

      {inbox.error && (
        <div className="mb-4">
          <ErrorBanner message={inbox.error} />
        </div>
      )}
      {action.error && (
        <div className="mb-4">
          <ErrorBanner message={action.error} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* ---- conversation list ---- */}
        <section className="overflow-hidden rounded-panel border border-line bg-card">
          <div className="border-b border-linesoft p-3">
            <label className="flex items-center gap-2 rounded-lg border border-line bg-inputbg px-2.5 py-1.5">
              <Search className="h-4 w-4 text-txfaint" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, number, message…"
                className="w-full bg-transparent text-sm text-tx outline-none placeholder:text-txfaint"
              />
            </label>
          </div>
          {inbox.loading ? (
            <LoadingBlock label="Loading conversations…" />
          ) : queue.length === 0 ? (
            <EmptyState
              icon={<Bot className="h-7 w-7" />}
              title="No conversations"
              description="When customers write to the bot, their chats appear here."
            />
          ) : (
            <ul className="max-h-[70vh] divide-y divide-linesoft overflow-y-auto">
              {queue.map((item) => {
                const human = item.handover_mode === 'human'
                const wait = human ? waitLabel(item.last_message_at) : null
                return (
                  <li key={item.wa_id}>
                    <button
                      type="button"
                      onClick={() => setSelected(item.wa_id)}
                      className={`block w-full px-4 py-3 text-left transition ${
                        selected === item.wa_id ? 'bg-accsoft' : 'hover:bg-paper'
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-tx">
                          {item.name || item.wa_id}
                        </span>
                        {human ? (
                          <span className="shrink-0 rounded-full bg-badbg px-2 py-0.5 text-[11px] font-semibold text-badink">
                            {wait ?? 'Waiting'}
                          </span>
                        ) : (
                          <span className="shrink-0 rounded-full bg-accsoft px-2 py-0.5 text-[11px] font-medium text-acc">
                            Bot
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-txmuted">
                        {stripRoute(item.last_message ?? '') || '—'}
                      </span>
                      <span className="mt-0.5 block text-[11px] text-txfaint">
                        {item.turns} messages
                        {item.assigned_agent_id
                          ? ` · with ${agentNames.get(item.assigned_agent_id) ?? item.assigned_agent_id}`
                          : ''}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {/* ---- conversation view ---- */}
        <section className="flex min-h-[480px] flex-col overflow-hidden rounded-panel border border-line bg-card">
          {!selected ? (
            <EmptyState
              icon={<UserRound className="h-7 w-7" />}
              title="Pick a conversation"
              description="Choose a chat on the left to read it and reply."
            />
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-linesoft px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-tx">{current?.name || selected}</p>
                  <p className="text-xs text-txmuted">
                    {selected}
                    {current?.handover_mode === 'human'
                      ? ' · a human is handling this chat'
                      : ' · the bot is handling this chat'}
                    {current?.assigned_agent_id
                      ? ` · assigned to ${agentNames.get(current.assigned_agent_id) ?? current.assigned_agent_id}`
                      : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {(agents.data ?? []).length > 0 && (
                    <select
                      aria-label="Assign to agent"
                      disabled={!canWrite}
                      defaultValue=""
                      onChange={(e) => void assign(e.target.value)}
                      className="rounded-lg border border-line bg-inputbg px-2 py-1.5 text-xs text-tx disabled:opacity-50"
                    >
                      <option value="">Assign…</option>
                      {(agents.data ?? []).map((a) => (
                        <option key={a.agent_id} value={a.agent_id}>
                          {a.username}
                        </option>
                      ))}
                    </select>
                  )}
                  {current?.handover_mode === 'human' ? (
                    <GhostButton disabled={!canWrite} onClick={() => void setMode('bot')}>
                      <Bot className="h-4 w-4" /> Give back to bot
                    </GhostButton>
                  ) : (
                    <GhostButton disabled={!canWrite} onClick={() => void setMode('human')}>
                      <UserRound className="h-4 w-4" /> Take over
                    </GhostButton>
                  )}
                  <GhostButton disabled={!canWrite} onClick={() => void resolve()}>
                    <CheckCheck className="h-4 w-4" /> Resolve
                  </GhostButton>
                </div>
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
                {thread.loading ? (
                  <LoadingBlock label="Loading messages…" />
                ) : (thread.data?.messages ?? []).length === 0 && failedHere.length === 0 ? (
                  <p className="text-sm text-txmuted">No messages in this conversation yet.</p>
                ) : (
                  <>
                    {(thread.data?.messages ?? []).map((turn, i) => (
                      <div key={i} className="space-y-1.5">
                        {turn.message && (
                          <div className="flex">
                            <div className="max-w-[75%] rounded-panel rounded-bl-sm bg-bubblein px-3.5 py-2.5 text-sm text-tx">
                              {stripRoute(turn.message)}
                            </div>
                          </div>
                        )}
                        {turn.response &&
                          (turn.route === 'agent' ? (
                            <div className="flex justify-end">
                              <div className="max-w-[75%]">
                                <div className="rounded-panel rounded-br-sm border border-dashed border-attline bg-card px-3.5 py-2.5 text-sm text-tx">
                                  {turn.response}
                                </div>
                                <p className="mt-1 text-right text-[11px] text-txmuted">Team reply</p>
                              </div>
                            </div>
                          ) : (
                            <div className="flex justify-end">
                              <div className="max-w-[75%]">
                                <div className="rounded-panel rounded-br-sm bg-acc px-3.5 py-2.5 text-sm text-onacc">
                                  {turn.response}
                                </div>
                                <p className="mt-1 text-right text-[11px] text-txmuted">Bot</p>
                              </div>
                            </div>
                          ))}
                      </div>
                    ))}
                    {failedHere.map((f) => (
                      <div key={f.id} className="flex justify-end">
                        <div className="max-w-[75%]">
                          <div className="rounded-panel rounded-br-sm border border-badline bg-badbg px-3.5 py-2.5 text-sm text-tx">
                            {f.text}
                          </div>
                          <p className="mt-1 flex items-center justify-end gap-2 text-right text-[11px] text-badink">
                            Not sent — {f.reason}
                            <button
                              type="button"
                              onClick={() => void retry(f)}
                              className="inline-flex items-center gap-1 font-semibold underline"
                            >
                              <RotateCcw className="h-3 w-3" /> Retry
                            </button>
                          </p>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </div>

              <div className="border-t border-linesoft px-4 py-3">
                {attachment && (
                  <div className="mb-2">
                    <AttachmentChip attachment={attachment} onRemove={() => setAttachment(null)} />
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <input
                    ref={fileRef}
                    type="file"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) void upload(f)
                      e.target.value = ''
                    }}
                  />
                  <button
                    type="button"
                    aria-label="Attach a file"
                    disabled={!canWrite}
                    onClick={() => fileRef.current?.click()}
                    className="rounded-lg border border-line bg-card p-2.5 text-txmuted transition hover:text-tx disabled:opacity-50"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={2}
                    disabled={!canWrite}
                    placeholder={
                      canWrite ? 'Write a reply…' : 'View-only — replies are disabled for super admin'
                    }
                    className="flex-1 resize-none rounded-lg border border-line bg-inputbg px-3 py-2 text-sm text-tx outline-none placeholder:text-txfaint disabled:opacity-60"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void handleSend()
                    }}
                  />
                  <PrimaryButton
                    disabled={!canWrite || (!draft.trim() && !attachment)}
                    onClick={() => void handleSend()}
                  >
                    <SendHorizonal className="h-4 w-4" /> Send
                  </PrimaryButton>
                </div>
                {current?.handover_mode !== 'human' && canWrite && (
                  <p className="mt-2 text-[11px] text-txmuted">
                    The bot is answering this chat. Take over before replying if you do not want the bot to
                    keep answering.
                  </p>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {inbox.data && (
        <p className="mt-3 text-xs text-txfaint">
          {inbox.data.count} conversations · {inbox.data.handoff_count} waiting for a human
        </p>
      )}
    </div>
  )
}
