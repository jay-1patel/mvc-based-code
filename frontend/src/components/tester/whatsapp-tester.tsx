import { useState } from 'react'
import { FlaskConical, Play, RotateCcw, Send } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import {
  testSimulatorApi,
  type CapturedMessage,
  type SimulatorResult,
  type SimulatorUserType,
} from '@/lib/test-simulator'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Alert } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

interface Turn {
  role: 'user' | 'bot'
  text: string
  meta?: string
  raw?: CapturedMessage[]
}

function formatCaptured(messages: CapturedMessage[] | undefined): string {
  if (!messages?.length) return '(no outbound messages captured)'
  return messages
    .map((m) => {
      const kind = m.type || 'text'
      const main = m.text || m.body || m.header || ''
      if (kind === 'buttons' && m.buttons?.length) {
        const labels = m.buttons
          .map((b) => b.title || b.reply?.title || b.id || b.reply?.id)
          .filter(Boolean)
          .join(' · ')
        return `${main}\n[buttons: ${labels}]`
      }
      if (kind === 'list' && m.sections?.length) {
        const rows = m.sections
          .flatMap((s) => (s.rows || []).map((r) => `• ${r.title || r.id}`))
          .join('\n')
        return `${m.header ? m.header + '\n' : ''}${main}\n${rows}`
      }
      return main || JSON.stringify(m)
    })
    .join('\n\n---\n\n')
}

export function WhatsAppTester({ tenantId }: { tenantId: string }) {
  const action = useAction()
  const [waId, setWaId] = useState('919876543210')
  const [userType, setUserType] = useState<SimulatorUserType>('b2c')
  const [draft, setDraft] = useState('Hi')
  const [turns, setTurns] = useState<Turn[]>([])
  const [last, setLast] = useState<SimulatorResult | null>(null)

  const appendResult = (userText: string, result: SimulatorResult) => {
    setLast(result)
    setTurns((prev) => [
      ...prev,
      { role: 'user', text: userText },
      {
        role: 'bot',
        text: formatCaptured(result.messages),
        meta: [
          result.status,
          result.new_state,
          result.user_type,
          result.human_handover ? 'handover' : null,
          'capture-only',
        ]
          .filter(Boolean)
          .join(' · '),
        raw: result.messages,
      },
    ])
  }

  const send = async (text?: string) => {
    const message = (text ?? draft).trim()
    if (!message) return
    const result = await action.run(() =>
      testSimulatorApi.send({
        wa_id: waId.trim() || '919876543210',
        message,
        user_type: userType,
        tenant_id: tenantId,
      }),
    )
    if (result) {
      appendResult(message, result)
      setDraft('')
    }
  }

  const reset = async () => {
    const result = await action.run(() =>
      testSimulatorApi.reset({
        wa_id: waId.trim() || '919876543210',
        tenant_id: tenantId,
        user_type: userType,
      }),
    )
    if (result) {
      setTurns([])
      setLast(null)
    }
  }

  const quickTap = (id: string) => {
    void send(id)
  }

  const lastButtons =
    last?.messages?.flatMap((m) => {
      if (m.type === 'buttons' && m.buttons) {
        return m.buttons.map((b) => ({
          id: b.id || b.reply?.id || '',
          title: b.title || b.reply?.title || b.id || '',
        }))
      }
      if (m.type === 'list' && m.sections) {
        return m.sections.flatMap((s) =>
          (s.rows || []).map((r) => ({ id: r.id || '', title: r.title || r.id || '' })),
        )
      }
      return []
    }) ?? []

  return (
    <div>
      <PageHeader
        title="WhatsApp Tester"
        description="Runs the real bot pipeline for this tenant. Replies are capture-only — nothing is sent through send2.digital."
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
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => void reset()} loading={action.busy} icon={<RotateCcw className="h-3.5 w-3.5" />}>
                Reset chat
              </Button>
              <Button variant="ghost" size="sm" onClick={() => void send('Hi')} loading={action.busy} icon={<Play className="h-3.5 w-3.5" />}>
                Send Hi
              </Button>
            </div>
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
            title="Conversation"
            description="Outbound messages the bot would have sent on WhatsApp."
          />
          <CardBody className="space-y-4">
            <div className="max-h-[420px] space-y-3 overflow-y-auto rounded-lg bg-surface-panel p-3 ring-1 ring-inset ring-surface-line">
              {turns.length === 0 ? (
                <p className="py-10 text-center text-sm text-slate-500">
                  Send <strong>Hi</strong> to open the main menu for tenant <code>{tenantId}</code>.
                </p>
              ) : (
                turns.map((turn, i) => (
                  <div key={i} className={`flex ${turn.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={
                        turn.role === 'user'
                          ? 'max-w-[85%] rounded-xl bg-accent-600 px-3 py-2 text-sm text-white'
                          : 'max-w-[85%] whitespace-pre-wrap rounded-xl bg-surface-raised px-3 py-2 text-sm text-slate-200 ring-1 ring-inset ring-surface-line'
                      }
                    >
                      {turn.text}
                      {turn.meta && (
                        <span className="mt-1 block text-2xs opacity-70">{turn.meta}</span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>

            {lastButtons.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {lastButtons
                  .filter((b) => b.id)
                  .map((b) => (
                    <button
                      key={b.id}
                      type="button"
                      onClick={() => quickTap(b.id)}
                      className="rounded-lg bg-surface-raised px-2.5 py-1 text-xs text-slate-300 ring-1 ring-inset ring-surface-line hover:bg-surface-panel"
                    >
                      {b.title || b.id}
                    </button>
                  ))}
              </div>
            )}

            <div className="flex gap-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    void send()
                  }
                }}
                placeholder="Type a customer message… (⌘/Ctrl+Enter to send)"
                rows={2}
                className="flex-1"
              />
              <Button
                variant="primary"
                onClick={() => void send()}
                loading={action.busy}
                icon={<Send className="h-4 w-4" />}
              >
                Send
              </Button>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

export default WhatsAppTester
