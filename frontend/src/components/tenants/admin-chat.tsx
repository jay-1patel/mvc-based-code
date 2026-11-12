import { useEffect, useRef, useState } from 'react'
import { Paperclip, Send, Trash2, Users, Wifi, WifiOff } from 'lucide-react'
import { api, getToken } from '@/lib/api'
import { attachmentsApi, ATTACHMENT_ACCEPT, ATTACHMENT_TYPES_LABEL, MAX_ATTACHMENT_LABEL, type Attachment } from '@/lib/attachments'
import { useAuth } from '@/lib/auth'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import {
  EmptyState,
  ErrorBanner,
  FieldTextarea,
  IconButton,
  Notice,
  Pill,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { AttachmentBubble, AttachmentChip } from './chat-attachment'

interface ChatUser {
  username: string
  chat_id: string
  last_message: { username: string; message: string; created_at: string; attachment?: Attachment | null } | null
}

interface ChatMessage {
  id: number
  chat_id: string
  username: string
  message: string
  created_at: string
  attachment?: Attachment | null
}

const WS_RETRY_MS = 3000
const PING_MS = 25_000

/**
 * Internal chat between the tenant's admins.
 *
 * A web socket carries live messages and presence for this tenant only; the
 * server keys rooms by tenant, so no other brand's staff can see this thread.
 * The REST twin (`/chat/send`) exists for callers that cannot hold a socket.
 */
export function AdminChatPanel({ tenantId }: { tenantId: string }) {
  const { can, identity } = useAuth()
  const username = identity?.username ?? ''

  const [users, setUsers] = useState<ChatUser[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [online, setOnline] = useState<string[]>([])
  const [connected, setConnected] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [threads, setThreads] = useState<Record<string, ChatMessage[]>>({})
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Attachment | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const socket = useRef<WebSocket | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const selectedChat = users.find((u) => u.username === selected)?.chat_id ?? null
  const selectedRef = useRef<string | null>(null)
  selectedRef.current = selectedChat

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .get<{ users: ChatUser[] }>(`/api/admin/tenants/${encodeURIComponent(tenantId)}/chat/users`)
      .then((r) => {
        if (cancelled) return
        setUsers(r.users)
        setLoadError(null)
        if (!selected && r.users.length) setSelected(r.users[0].username)
      })
      .catch((err) => !cancelled && setLoadError((err as Error).message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId])

  useEffect(() => {
    if (!can('chat') || !username) return
    let closed = false
    let retry: number | undefined

    const connect = () => {
      const token = getToken() ?? ''
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const url =
        `${scheme}://${window.location.host}/api/admin/tenants/${encodeURIComponent(tenantId)}` +
        `/chat/ws/${encodeURIComponent(username)}?token=${encodeURIComponent(token)}&conn_id=console`
      const ws = new WebSocket(url)
      socket.current = ws

      ws.onopen = () => {
        setConnected(true)
        if (selectedRef.current) {
          ws.send(JSON.stringify({ type: 'load_history', chat_id: selectedRef.current }))
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = window.setTimeout(connect, WS_RETRY_MS)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (event) => {
        let data: Record<string, unknown>
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }
        if (data.type === 'online_users') {
          setOnline((data.users as string[]) ?? [])
        } else if (data.type === 'history') {
          const chatId = data.chat_id as string
          setThreads((prev) => ({ ...prev, [chatId]: (data.messages as ChatMessage[]) ?? [] }))
        } else if (data.type === 'message') {
          const msg = data as unknown as ChatMessage
          setThreads((prev) => ({ ...prev, [msg.chat_id]: [...(prev[msg.chat_id] ?? []), msg] }))
          setUsers((prev) =>
            prev.map((u) =>
              u.chat_id === msg.chat_id
                ? {
                    ...u,
                    last_message: {
                      username: msg.username,
                      message: msg.message,
                      created_at: msg.created_at,
                      attachment: msg.attachment,
                    },
                  }
                : u,
            ),
          )
        } else if (data.type === 'delete_message') {
          const id = data.message_id as number
          setThreads((prev) => {
            const next: Record<string, ChatMessage[]> = {}
            for (const [key, list] of Object.entries(prev)) next[key] = list.filter((m) => m.id !== id)
            return next
          })
        }
      }
    }

    connect()
    const ping = window.setInterval(() => {
      if (socket.current?.readyState === WebSocket.OPEN) {
        socket.current.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_MS)

    return () => {
      closed = true
      window.clearInterval(ping)
      if (retry) window.clearTimeout(retry)
      socket.current?.close()
    }
  }, [tenantId, username, can])

  useEffect(() => {
    if (selectedChat && socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'load_history', chat_id: selectedChat }))
    }
  }, [selectedChat])

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    setUploadError(null)
    try {
      setPending(await attachmentsApi.upload(tenantId, file, 'chat'))
    } catch (err) {
      setUploadError((err as Error).message)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const send = () => {
    const text = draft.trim()
    const attachment = pending
    if ((!text && !attachment) || !selectedChat) return
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'message', chat_id: selectedChat, text, attachment }))
    } else {
      // Socket down: fall back to the REST twin so the message is not lost.
      void api.post(`/api/admin/tenants/${encodeURIComponent(tenantId)}/chat/send`, {
        chat_id: selectedChat,
        text,
        attachment,
      })
    }
    setDraft('')
    setPending(null)
  }

  const removeMessage = (id: number) => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: 'delete_message', message_id: id, scope: 'everyone' }))
    }
  }

  if (!can('chat')) {
    return (
      <div>
        <PageHeader title="Admin chat" />
        <Notice tone="warn" title="Not permitted">
          The internal chat needs the <strong>chat</strong> permission.
        </Notice>
      </div>
    )
  }

  const messages = selectedChat ? (threads[selectedChat] ?? []) : []

  return (
    <div>
      <PageHeader
        title="Admin chat"
        description="Talk to the other admins of this tenant. Messages are private to this brand."
        meta={
          <>
            <Pill tone={connected ? 'ok' : 'bad'}>
              <span className="inline-flex items-center gap-1">
                {connected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                {connected ? 'live' : 'reconnecting'}
              </span>
            </Pill>
            <Pill>{online.length} online</Pill>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        <section className="overflow-hidden rounded-panel border border-line bg-card">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3">
            <span className="text-txmuted">
              <Users className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-tx">People</h2>
          </div>
          {loading ? (
            <LoadingBlock label="Loading admins…" />
          ) : loadError ? (
            <div className="p-4">
              <ErrorBanner title="Could not load" message={loadError} />
            </div>
          ) : users.length === 0 ? (
            <EmptyState title="No one else yet" description="Add another admin to start a conversation." />
          ) : (
            <ul className="divide-y divide-linesoft">
              {users.map((user) => {
                const isOnline = online.includes(user.username)
                return (
                  <li key={user.username}>
                    <button
                      type="button"
                      onClick={() => setSelected(user.username)}
                      className={cn(
                        'w-full px-4 py-3 text-left transition',
                        user.username === selected ? 'bg-accsoft' : 'hover:bg-paper',
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={cn('h-1.5 w-1.5 shrink-0 rounded-full', isOnline ? 'bg-ok' : 'bg-line')}
                        />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-tx">
                          {user.username}
                        </span>
                      </div>
                      {user.last_message && (
                        <p className="mt-1 truncate text-[11px] text-txmuted">{user.last_message.message}</p>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="flex min-h-[26rem] flex-col rounded-panel border border-line bg-card">
          {!selected ? (
            <EmptyState
              className="flex-1"
              title="Pick someone"
              description="Choose a teammate to open the conversation."
            />
          ) : (
            <>
              <div className="border-b border-linesoft px-4 py-3 sm:px-5">
                <h2 className="truncate text-sm font-semibold text-tx">{selected}</h2>
                <p className="text-xs text-txmuted">{connected ? 'Connected' : 'Reconnecting…'}</p>
              </div>
              <div className="flex-1 space-y-2 overflow-y-auto px-4 py-4">
                {messages.length === 0 ? (
                  <p className="py-8 text-center text-sm text-txmuted">No messages yet. Say hello.</p>
                ) : (
                  messages.map((msg) => {
                    const mine = msg.username === username
                    return (
                      <div key={msg.id} className={cn('group flex', mine ? 'justify-end' : 'justify-start')}>
                        <div
                          className={cn(
                            'max-w-[75%] rounded-panel px-3.5 py-2.5 text-sm',
                            mine
                              ? 'rounded-br-sm bg-acc text-onacc'
                              : 'rounded-bl-sm bg-bubblein text-tx',
                          )}
                        >
                          {!mine && <p className="mb-0.5 text-[11px] font-medium opacity-70">{msg.username}</p>}
                          {msg.attachment?.url && (
                            <div className={cn(msg.message && 'mb-1.5')}>
                              <AttachmentBubble attachment={msg.attachment} mine={mine} />
                            </div>
                          )}
                          {msg.message && <p className="whitespace-pre-wrap break-words">{msg.message}</p>}
                          <span className="mt-1 flex items-center justify-between gap-2 text-[11px] opacity-70">
                            {formatDate(msg.created_at)}
                            {mine && (
                              <button
                                type="button"
                                onClick={() => removeMessage(msg.id)}
                                className="opacity-0 transition group-hover:opacity-100"
                                title="Delete for everyone"
                              >
                                <Trash2 className="h-3 w-3" />
                              </button>
                            )}
                          </span>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
              <div className="border-t border-linesoft p-4">
                {uploadError && <p className="mb-2 text-xs text-badink">{uploadError}</p>}
                {pending && (
                  <div className="mb-2">
                    <AttachmentChip attachment={pending} onRemove={() => setPending(null)} />
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <input
                    ref={fileInput}
                    type="file"
                    className="hidden"
                    accept={ATTACHMENT_ACCEPT}
                    onChange={(e) => void pickFile(e.target.files?.[0])}
                  />
                  <IconButton
                    onClick={() => fileInput.current?.click()}
                    disabled={uploading}
                    title="Attach a file"
                  >
                    <Paperclip className={cn('h-4 w-4', uploading && 'animate-pulse')} />
                  </IconButton>
                  <div className="flex-1">
                    <FieldTextarea
                      rows={2}
                      value={draft}
                      placeholder={`Message ${selected}…`}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault()
                          send()
                        }
                      }}
                    />
                  </div>
                  <PrimaryButton
                    onClick={send}
                    disabled={(!draft.trim() && !pending) || uploading}
                  >
                    <Send className="h-4 w-4" />
                    Send
                  </PrimaryButton>
                </div>
                <p className="mt-1 text-[11px] text-txmuted">
                  {ATTACHMENT_TYPES_LABEL} · Max {MAX_ATTACHMENT_LABEL} per file
                </p>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
