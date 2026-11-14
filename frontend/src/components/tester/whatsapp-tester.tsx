import { Fragment, useEffect, useRef, useState } from 'react'
import {
  CheckCheck,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  FlaskConical,
  List,
  Play,
  RotateCcw,
  Send,
  X,
} from 'lucide-react'
import { useAction } from '@/lib/hooks'
import {
  testSimulatorApi,
  type CapturedMessage,
  type PhoneMessage,
  type SimulatorResult,
  type SimulatorUserType,
} from '@/lib/test-simulator'
import { parseWhatsAppFormat, splitLinks } from '@/lib/whatsapp-format'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Alert } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

/**
 * WhatsApp Tester — a replica console.
 *
 * The phone below renders the backend's delivery-shaped phone messages
 * (SimulatorResult.whatsapp): the exact sequence a customer receives on
 * WhatsApp, with WhatsApp formatting, interactive limits and body splits
 * already applied by the delivery layer. Buttons and list rows are
 * tappable and feed their payload back through the same orchestrator, so
 * a conversation here behaves the way it will on a real phone. Everything
 * is capture-only — nothing is ever sent through send2.digital.
 */

interface BotTurn {
  role: 'bot'
  phone: PhoneMessage[]
  raw: CapturedMessage[]
  at: string
}

interface UserTurn {
  role: 'user'
  text: string
  at: string
}

type Turn = BotTurn | UserTurn

type ListPhoneMessage = Extract<PhoneMessage, { kind: 'list' }>

const clock = () =>
  new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

/** Degradation path for older backends that return only raw captures. */
function fallbackPhone(messages: CapturedMessage[] | undefined): PhoneMessage[] {
  if (!messages?.length) return []
  const out: PhoneMessage[] = []
  messages.forEach((m, i) => {
    if (m.type === 'buttons') {
      out.push({
        kind: 'buttons',
        text: m.text || m.body || '',
        buttons: (m.buttons || []).map((b) => ({
          id: b.id || b.reply?.id || '',
          title: b.title || b.reply?.title || '',
        })),
        source_index: i,
      })
    } else if (m.type === 'list') {
      out.push({
        kind: 'list',
        header: m.header || '',
        body: m.body || m.text || '',
        button_text: m.button_text || 'Show Options',
        sections: m.sections || [],
        source_index: i,
      })
    } else if (m.type === 'image') {
      out.push({ kind: 'image', media_url: String(m.media_url || ''), source_index: i })
    } else if (m.type === 'document') {
      out.push({
        kind: 'document',
        media_url: String(m.media_url || ''),
        filename: String(m.filename || ''),
        source_index: i,
      })
    } else if (m.type === 'cta_url') {
      out.push({
        kind: 'cta_url',
        text: m.text || '',
        display_text: String(m.display_text || 'Learn More'),
        url: String(m.cta_url || ''),
        image_url: String(m.image_url || ''),
        source_index: i,
      })
    } else {
      out.push({ kind: 'text', text: m.text || m.body || '', source_index: i })
    }
  })
  return out
}

/** Render WhatsApp markup (*bold*, _italic_, ~strike~, `mono`) + links. */
function FormattedText({ text }: { text: string }) {
  return (
    <>
      {text.split('\n').map((line, li) => (
        <span key={li}>
          {li > 0 && <br />}
          {parseWhatsAppFormat(line).flatMap((seg, si) =>
            splitLinks(seg).map((piece, pi) => {
              const key = `${si}-${pi}`
              const inner = piece.href ? (
                <a
                  href={piece.href}
                  target="_blank"
                  rel="noreferrer"
                  className="underline decoration-dotted underline-offset-2"
                >
                  {piece.text}
                </a>
              ) : (
                piece.text
              )
              if (seg.bold) return <strong key={key}>{inner}</strong>
              if (seg.italic) return <em key={key}>{inner}</em>
              if (seg.strike) return <s key={key}>{inner}</s>
              if (seg.mono)
                return (
                  <code key={key} className="rounded bg-bubblein px-1 font-mono text-[0.85em]">
                    {inner}
                  </code>
                )
              return <Fragment key={key}>{inner}</Fragment>
            }),
          )}
        </span>
      ))}
    </>
  )
}

function BubbleTime({ at, ticks }: { at: string; ticks?: boolean }) {
  return (
    <span className="mt-1 flex items-center justify-end gap-1 text-[10px] leading-none text-txfaint">
      {at}
      {ticks && <CheckCheck className="h-3.5 w-3.5 text-acc" aria-label="delivered" />}
    </span>
  )
}

function PhoneMessageView({
  msg,
  at,
  onButtonTap,
  onListOpen,
}: {
  msg: PhoneMessage
  at: string
  onButtonTap: (id: string, title: string) => void
  onListOpen: (msg: ListPhoneMessage) => void
}) {
  if (msg.kind === 'text') {
    return (
      <div className="max-w-[85%] rounded-lg rounded-tl-none bg-card px-2.5 py-1.5 text-sm text-tx shadow-sm ring-1 ring-line/60">
        <FormattedText text={msg.text} />
        <BubbleTime at={at} />
      </div>
    )
  }

  if (msg.kind === 'image') {
    return (
      <div className="max-w-[85%] rounded-lg rounded-tl-none bg-card p-1.5 shadow-sm ring-1 ring-line/60">
        <img
          src={msg.media_url}
          alt="Bot image"
          className="max-h-56 w-full rounded-md object-cover"
        />
        <BubbleTime at={at} />
      </div>
    )
  }

  if (msg.kind === 'document') {
    return (
      <div className="flex max-w-[85%] items-center gap-2.5 rounded-lg rounded-tl-none bg-card px-3 py-2.5 shadow-sm ring-1 ring-line/60">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-badbg text-badink">
          <FileText className="h-5 w-5" />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm text-tx">
            {msg.filename || 'Document'}
          </span>
          <span className="block text-xs text-txmuted">Document · tap on WhatsApp to open</span>
          <BubbleTime at={at} />
        </span>
      </div>
    )
  }

  if (msg.kind === 'buttons') {
    return (
      <div className="w-[85%] max-w-[85%]">
        <div className="rounded-lg rounded-tl-none bg-card px-2.5 py-1.5 text-sm text-tx shadow-sm ring-1 ring-line/60">
          <FormattedText text={msg.text} />
          <BubbleTime at={at} />
        </div>
        <div className="mt-1 overflow-hidden rounded-lg bg-card shadow-sm ring-1 ring-line/60">
          {msg.buttons.map((b, i) => (
            <button
              key={b.id || i}
              type="button"
              onClick={() => onButtonTap(b.id, b.title)}
              className={`block w-full px-3 py-2 text-center text-sm text-acc hover:bg-accsoft/60 ${
                i > 0 ? 'border-t border-linesoft' : ''
              }`}
            >
              {b.title}
            </button>
          ))}
        </div>
      </div>
    )
  }

  if (msg.kind === 'list') {
    return (
      <div className="max-w-[85%]">
        <div className="rounded-lg rounded-tl-none bg-card px-2.5 py-1.5 text-sm text-tx shadow-sm ring-1 ring-line/60">
          {msg.header.trim() && (
            <p className="font-semibold">
              <FormattedText text={msg.header} />
            </p>
          )}
          <FormattedText text={msg.body} />
          {msg.footer?.trim() && (
            <p className="mt-0.5 text-xs text-txmuted">{msg.footer}</p>
          )}
          <BubbleTime at={at} />
        </div>
        <button
          type="button"
          onClick={() => onListOpen(msg)}
          className="mt-1 flex w-full items-center justify-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-acc shadow-sm ring-1 ring-line/60 hover:bg-accsoft/60"
        >
          <List className="h-4 w-4" />
          {msg.button_text}
        </button>
      </div>
    )
  }

  // cta_url
  return (
    <div className="w-[85%] max-w-[85%]">
      <div className="overflow-hidden rounded-lg rounded-tl-none bg-card shadow-sm ring-1 ring-line/60">
        {msg.image_url && (
          <img src={msg.image_url} alt="" className="max-h-44 w-full object-cover" />
        )}
        <div className="px-2.5 py-1.5 text-sm text-tx">
          <FormattedText text={msg.text} />
          {msg.footer?.trim() && (
            <p className="mt-0.5 text-xs text-txmuted">{msg.footer}</p>
          )}
          <BubbleTime at={at} />
        </div>
      </div>
      <a
        href={msg.url}
        target="_blank"
        rel="noreferrer"
        className="mt-1 flex w-full items-center justify-center gap-1.5 rounded-lg bg-card px-3 py-2 text-sm text-acc shadow-sm ring-1 ring-line/60 hover:bg-accsoft/60"
      >
        <ExternalLink className="h-3.5 w-3.5" />
        {msg.display_text}
      </a>
    </div>
  )
}

/** WhatsApp's list picker, approximated as an in-phone bottom sheet. */
function ListSheet({
  msg,
  onPick,
  onClose,
}: {
  msg: ListPhoneMessage
  onPick: (id: string, title: string) => void
  onClose: () => void
}) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col justify-end">
      <button
        type="button"
        aria-label="Close list"
        className="absolute inset-0 bg-tx/40"
        onClick={onClose}
      />
      <div className="relative max-h-[75%] overflow-y-auto rounded-t-2xl bg-card shadow-xl">
        <div className="sticky top-0 flex items-center justify-between bg-card px-4 pb-1 pt-3">
          <p className="text-sm font-semibold text-tx">{msg.header.trim() || 'Choose an option'}</p>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-1 text-txmuted hover:bg-bubblein"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {msg.sections.map((section, si) => (
          <div key={si} className="px-2 pb-2">
            {section.title?.trim() && (
              <p className="px-2 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-acc">
                {section.title}
              </p>
            )}
            {(section.rows || []).map((row) => (
              <button
                key={row.id}
                type="button"
                onClick={() => onPick(row.id || '', row.title || row.id || '')}
                className="block w-full rounded-md px-2 py-2 text-left hover:bg-bubblein/70"
              >
                <span className="block text-sm text-tx">{row.title}</span>
                {row.description?.trim() && (
                  <span className="block text-xs text-txmuted">{row.description}</span>
                )}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export function WhatsAppTester({ tenantId }: { tenantId: string }) {
  const action = useAction()
  const [waId, setWaId] = useState('919876543210')
  const [userType, setUserType] = useState<SimulatorUserType>('b2c')
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [last, setLast] = useState<SimulatorResult | null>(null)
  const [sheet, setSheet] = useState<ListPhoneMessage | null>(null)
  const [rawAll, setRawAll] = useState(false)
  const [rawTurns, setRawTurns] = useState<Set<number>>(new Set())
  const chatRef = useRef<HTMLDivElement>(null)
  const openedFor = useRef<string | null>(null)

  const simWaId = () => waId.trim() || '919876543210'

  const appendResult = (displayText: string, result: SimulatorResult) => {
    setLast(result)
    const at = clock()
    setTurns((prev) => [
      ...prev,
      { role: 'user', text: displayText, at },
      {
        role: 'bot',
        phone: result.whatsapp ?? fallbackPhone(result.messages),
        raw: result.messages ?? [],
        at,
      },
    ])
  }

  const send = async (payload: string, displayText?: string) => {
    const message = payload.trim()
    if (!message) return
    const result = await action.run(() =>
      testSimulatorApi.send({
        wa_id: simWaId(),
        message,
        user_type: userType,
        tenant_id: tenantId,
      }),
    )
    if (result) {
      appendResult(displayText ?? payload, result)
      setDraft('')
    }
  }

  /** Reset, then play the exact new-customer opening (a first "Hi"). */
  const playOpening = async () => {
    const cleared = await action.run(() =>
      testSimulatorApi.reset({
        wa_id: simWaId(),
        tenant_id: tenantId,
        user_type: userType,
      }),
    )
    if (!cleared) return
    setTurns([])
    setLast(null)
    setSheet(null)
    setRawTurns(new Set())
    await send('Hi')
  }

  // On open (and on tenant switch) play the new-customer flow once.
  useEffect(() => {
    if (openedFor.current === tenantId) return
    openedFor.current = tenantId
    void playOpening()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId])

  useEffect(() => {
    const el = chatRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns, sheet])

  const toggleRaw = (turnIndex: number) =>
    setRawTurns((prev) => {
      const next = new Set(prev)
      if (next.has(turnIndex)) next.delete(turnIndex)
      else next.add(turnIndex)
      return next
    })

  const meta = last
    ? [
        last.status,
        last.new_state,
        last.user_type,
        last.human_handover ? 'human handover' : null,
        'capture-only',
      ]
        .filter(Boolean)
        .join(' · ')
    : null

  return (
    <div>
      <PageHeader
        title="WhatsApp Tester"
        description="A replica of the customer's WhatsApp. The phone renders the exact messages the bot delivers for this tenant — same menu, same answers — and every tap runs the real pipeline. Nothing is sent to a real phone."
        meta={
          <Badge tone="warning">
            <FlaskConical className="h-3 w-3" />
            capture-only
          </Badge>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card>
          <CardHeader title="Session" description="Simulated WhatsApp identity." />
          <CardBody className="space-y-3">
            <Input
              label="WhatsApp ID"
              value={waId}
              onChange={(e) => setWaId(e.target.value)}
              placeholder="9198…"
            />
            <Select
              label="Persona"
              value={userType}
              onChange={(e) => setUserType(e.target.value as SimulatorUserType)}
            >
              <option value="b2c">B2C customer</option>
              <option value="b2b">B2B distributor</option>
              <option value="auto">Auto (from DB)</option>
            </Select>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void playOpening()}
                loading={action.busy}
                icon={<RotateCcw className="h-3.5 w-3.5" />}
              >
                Restart as new customer
              </Button>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-txmuted">
              <input
                type="checkbox"
                checked={rawAll}
                onChange={(e) => setRawAll(e.target.checked)}
                className="h-4 w-4 accent-acc"
              />
              Show raw payload for every reply
            </label>
            {meta && (
              <p className="rounded-md bg-bubblein/70 px-2 py-1.5 text-xs text-txmuted">
                Last reply: {meta}
              </p>
            )}
            <p className="text-xs leading-relaxed text-txmuted">
              The phone shows the delivery-shaped messages — WhatsApp
              formatting, button/list limits and long-message splits already
              applied — exactly as the customer will see them. Test here,
              then send the same messages from a real phone: the replies
              will match.
            </p>
            {action.error && (
              <Alert tone="danger" title="Simulator error">
                {action.error}
                {action.error.includes('502') || action.error.toLowerCase().includes('send')
                  ? ' — Tester is capture-only; if you still see Send2 errors, restart the backend so capture_only is loaded.'
                  : ''}
              </Alert>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Replica phone"
            description={`Conversation for tenant ${tenantId}, rendered as WhatsApp renders it.`}
          />
          <CardBody>
            <div className="mx-auto w-full max-w-[400px] rounded-[2rem] bg-tx p-2 shadow-lg">
              <div className="relative overflow-hidden rounded-[1.6rem] bg-card">
                {/* WhatsApp header bar */}
                <div className="flex items-center gap-3 bg-acc px-4 py-3 text-onacc">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-onacc/20 text-sm font-semibold uppercase">
                    {tenantId.slice(0, 1)}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{tenantId}</span>
                    <span className="block text-xs opacity-80">online</span>
                  </span>
                  <Badge tone="warning" className="ml-auto shrink-0">
                    test
                  </Badge>
                </div>

                {/* Chat area */}
                <div
                  ref={chatRef}
                  className="h-[520px] space-y-2 overflow-y-auto bg-bubblein px-3 py-3"
                  style={{
                    backgroundImage:
                      'radial-gradient(rgb(var(--line) / 0.3) 1px, transparent 1.2px)',
                    backgroundSize: '18px 18px',
                  }}
                >
                  {turns.length === 0 ? (
                    <p className="py-16 text-center text-sm text-txmuted">
                      Opening the chat as a new customer…
                    </p>
                  ) : (
                    turns.map((turn, i) => {
                      if (turn.role === 'user') {
                        return (
                          <div key={i} className="flex justify-end">
                            <div className="max-w-[85%] rounded-lg rounded-tr-none bg-accsoft px-2.5 py-1.5 text-sm text-tx shadow-sm">
                              <FormattedText text={turn.text} />
                              <BubbleTime at={turn.at} ticks />
                            </div>
                          </div>
                        )
                      }
                      const showRaw = rawAll || rawTurns.has(i)
                      return (
                        <div key={i} className="space-y-1.5">
                          {turn.phone.length === 0 ? (
                            <div className="max-w-[85%] rounded-lg rounded-tl-none bg-card px-2.5 py-1.5 text-sm italic text-txmuted shadow-sm ring-1 ring-line/60">
                              (the bot sent nothing for this message)
                              <BubbleTime at={turn.at} />
                            </div>
                          ) : (
                            turn.phone.map((msg, j) => (
                              <div key={j} className="flex justify-start">
                                <PhoneMessageView
                                  msg={msg}
                                  at={turn.at}
                                  onButtonTap={(id, title) => void send(id, title)}
                                  onListOpen={setSheet}
                                />
                              </div>
                            ))
                          )}
                          <button
                            type="button"
                            onClick={() => toggleRaw(i)}
                            className="flex items-center gap-1 pl-1 text-[11px] text-txfaint hover:text-txmuted"
                          >
                            {showRaw ? (
                              <EyeOff className="h-3 w-3" />
                            ) : (
                              <Eye className="h-3 w-3" />
                            )}
                            {showRaw ? 'hide raw payload' : 'view raw payload'}
                          </button>
                          {showRaw && (
                            <pre className="max-h-64 overflow-auto rounded-md bg-tx p-2 text-[11px] leading-relaxed text-paper">
                              {JSON.stringify(turn.raw, null, 2)}
                            </pre>
                          )}
                        </div>
                      )
                    })
                  )}
                </div>

                {/* Composer */}
                <div className="flex items-end gap-2 border-t border-line bg-card px-3 py-2.5">
                  <Textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        void send(draft)
                      }
                    }}
                    placeholder="Type a message"
                    rows={1}
                    className="flex-1"
                  />
                  <Button
                    variant="primary"
                    onClick={() => void send(draft)}
                    loading={action.busy}
                    icon={<Send className="h-4 w-4" />}
                    aria-label="Send message"
                  >
                    Send
                  </Button>
                </div>

                {sheet && (
                  <ListSheet
                    msg={sheet}
                    onClose={() => setSheet(null)}
                    onPick={(id, title) => {
                      setSheet(null)
                      void send(id, title)
                    }}
                  />
                )}
              </div>
            </div>

            <p className="mx-auto mt-3 flex max-w-[400px] items-center gap-1.5 text-xs text-txmuted">
              <Play className="h-3.5 w-3.5 shrink-0" />
              Restart replays the new-customer opening: reset, then “Hi”, so
              the welcome and menu appear exactly as a first-time customer
              sees them.
            </p>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

export default WhatsAppTester
