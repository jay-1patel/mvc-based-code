import { useState, useEffect } from 'react'
import { CalendarCheck, CheckCircle2, Loader2, PhoneCall, Plus, Video, XCircle } from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import {
  CALLBACK_PRIORITIES,
  CALLBACK_STATUSES,
  CALLBACK_TYPES,
  callbacksApi,
  type Callback,
  type CallbackInput,
} from '@/lib/callbacks'
import { formatDate, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import {
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  FieldTextarea,
  GhostButton,
  NeedRow,
  Panel,
  Pill,
  PrimaryButton,
  type PillTone,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useConfirm } from '@/components/ui/confirm'

const FILTERS = ['all', ...CALLBACK_STATUSES]

const STATUS_TONE: Record<string, PillTone> = {
  pending: 'warn',
  confirmed: 'ok',
  scheduled: 'ok',
  in_progress: 'neutral',
  completed: 'ok',
  cancelled: 'neutral',
  no_show: 'bad',
}

const PRIORITY_TONE: Record<string, PillTone> = {
  low: 'neutral',
  medium: 'neutral',
  high: 'warn',
  urgent: 'bad',
}

const EMPTY_FORM: CallbackInput = {
  customer_name: '',
  wa_id: '',
  callback_type: 'general',
  priority: 'medium',
  purpose: '',
  customer_phone: '',
  preferred_date: '',
  preferred_time: '',
  additional_info: '',
}

/** True while the callback can still be acted on. */
const OPEN = new Set(['pending', 'confirmed', 'scheduled', 'in_progress'])

/**
 * Callback requests customers raise through the bot, and the agent-side
 * lifecycle: schedule a Google Meet, mark the call done, or cancel.
 */
export function CallbacksPanel({ tenantId }: { tenantId: string }) {
  const toast = useToast()
  const confirmDialog = useConfirm()
  const action = useAction()
  const createAction = useAction()

  const [status, setStatus] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<CallbackInput>(EMPTY_FORM)
  const [notes, setNotes] = useState('')
  const [schedule, setSchedule] = useState({ agent_name: '', agent_id: '', start: '', end: '' })

  const state = useAsync(
    (signal) => callbacksApi.list(tenantId, { status: status === 'all' ? undefined : status }, signal),
    [tenantId, status],
  )

  const callbacks = state.data?.callbacks ?? []
  const active: Callback | null = callbacks.find((c) => c.id === selected) ?? null
  const refresh = () => state.reload()

  const openCount = callbacks.filter((c) => OPEN.has(c.status)).length

  const update = (patch: Partial<CallbackInput>) => setForm((f) => ({ ...f, ...patch }))

  const isValidPhone = (val: string) => {
    const raw = val.trim()
    if (!/^\+?[\d\s\-()]+$/.test(raw)) return false
    const digits = raw.replace(/\D/g, '')
    if (digits.length === 10) return true
    if (digits.startsWith('0') && digits.length === 11) return true
    if (raw.startsWith('+') && digits.length >= 11 && digits.length <= 13) return true
    if (digits.startsWith('91') && digits.length === 12) return true
    return false
  }

  const create = async () => {
    if (!form.customer_name.trim() || !form.wa_id.trim()) return
    if (form.customer_phone?.trim() && !isValidPhone(form.customer_phone)) {
      toast.push('Please enter a valid 10-digit phone number (or country code + 10 digits).', 'error')
      return
    }
    const result = await createAction.run(() => callbacksApi.create(tenantId, form), 'Callback created')
    if (result) {
      setForm(EMPTY_FORM)
      setShowForm(false)
      refresh()
    }
  }

  const cancel = async () => {
    if (!active) return
    if (
      !(await confirmDialog({
        message: `Cancel the callback from ${active.customer_name}? The customer can be notified.`,
        confirmLabel: 'Cancel callback',
      }))
    )
      return
    const result = await action.run(() => callbacksApi.cancel(tenantId, active.id), 'Callback cancelled')
    if (result) refresh()
  }

  const complete = async () => {
    if (!active) return
    const result = await action.run(
      () => callbacksApi.complete(tenantId, active.id, notes),
      'Callback completed',
    )
    if (result) {
      setNotes('')
      refresh()
    }
  }

  useEffect(() => {
    if (active) {
      const now = new Date()
      now.setMinutes(0, 0, 0)
      now.setHours(now.getHours() + 1)
      const end = new Date(now.getTime() + 30 * 60000)

      const toLocalInput = (d: Date) => {
        const offset = d.getTimezoneOffset() * 60000
        return new Date(d.getTime() - offset).toISOString().slice(0, 16)
      }

      setSchedule((s) => ({
        ...s,
        start: s.start || toLocalInput(now),
        end: s.end || toLocalInput(end),
      }))
    }
  }, [active?.id])

  const book = async () => {
    if (!active) return
    const agentName = schedule.agent_name.trim() || 'Support Agent'
    const agentId = schedule.agent_id.trim() || agentName
    if (!schedule.start || !schedule.end) {
      toast.push('Please select both a Start and End time for the meeting.', 'error')
      return
    }
    const result = await action.run(
      () =>
        callbacksApi.schedule(tenantId, active.id, {
          agent_id: agentId,
          agent_name: agentName,
          start_time: new Date(schedule.start).toISOString(),
          end_time: new Date(schedule.end).toISOString(),
        }),
      'Callback scheduled — Google Meet link generated and sent to customer on WhatsApp',
    )
    if (result) {
      setSchedule({ agent_name: '', agent_id: '', start: '', end: '' })
      refresh()
    }
  }

  return (
    <div>
      <PageHeader
        title="Callbacks"
        description="Call-back requests customers raise through the bot. Schedule them with a Google Meet link, mark them done, or cancel."
        meta={
          state.data && (
            <>
              <Pill tone={openCount ? 'warn' : 'ok'}>{openCount} open</Pill>
              <Pill>{state.data.total_count} total</Pill>
            </>
          )
        }
        actions={
          <PrimaryButton onClick={() => setShowForm((v) => !v)}>
            <Plus className="h-4 w-4" />
            New callback
          </PrimaryButton>
        }
      />

      {openCount > 0 && (
        <Panel title="Needs you" className="mb-4">
          <NeedRow icon={<PhoneCall className="h-4 w-4" />}>
            <strong>{openCount}</strong> {openCount === 1 ? 'callback is' : 'callbacks are'} still open in the list
            below — schedule, complete or cancel each one.
          </NeedRow>
        </Panel>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {FILTERS.map((filter) => (
          <button
            key={filter}
            type="button"
            onClick={() => {
              setStatus(filter)
              setSelected(null)
            }}
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition',
              status === filter
                ? 'bg-acc text-onacc'
                : 'text-txmuted hover:bg-paper hover:text-tx',
            )}
          >
            {filter.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {showForm && (
        <section className="mb-4 rounded-panel border border-line bg-card">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3 sm:px-5">
            <span className="text-txmuted">
              <PhoneCall className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-tx">New callback request</h2>
          </div>
          <div className="grid gap-3 p-4 sm:grid-cols-2 sm:p-5">
            <FieldInput
              label="Customer name"
              value={form.customer_name}
              onChange={(e) => update({ customer_name: e.target.value })}
              placeholder="Jane Doe"
            />
            <FieldInput
              label="WhatsApp number"
              value={form.wa_id}
              onChange={(e) => update({ wa_id: e.target.value })}
              placeholder="919876543210"
            />
            <FieldInput
              label="Phone (optional)"
              value={form.customer_phone}
              onChange={(e) => update({ customer_phone: e.target.value })}
              placeholder="+91 98765 43210"
            />
            <FieldSelect
              label="Type"
              value={form.callback_type}
              onChange={(e) => update({ callback_type: e.target.value })}
            >
              {CALLBACK_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, ' ')}
                </option>
              ))}
            </FieldSelect>
            <FieldSelect
              label="Priority"
              value={form.priority}
              onChange={(e) => update({ priority: e.target.value })}
            >
              {CALLBACK_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </FieldSelect>
            <FieldInput
              label="Preferred date"
              type="date"
              value={form.preferred_date ?? ''}
              onChange={(e) => update({ preferred_date: e.target.value })}
            />
            <FieldInput
              label="Preferred time"
              value={form.preferred_time ?? ''}
              onChange={(e) => update({ preferred_time: e.target.value })}
              placeholder="10:00 or 10:00-11:00"
            />
            <div className="sm:col-span-2">
              <FieldInput
                label="Purpose"
                value={form.purpose}
                onChange={(e) => update({ purpose: e.target.value })}
                placeholder="What should the call be about?"
              />
            </div>
            <div className="sm:col-span-2">
              <FieldTextarea
                label="Additional info"
                value={form.additional_info}
                onChange={(e) => update({ additional_info: e.target.value })}
                placeholder="Context from the customer…"
              />
            </div>
            {(createAction.error || action.error) && (
              <div className="sm:col-span-2">
                <ErrorBanner title="Action failed" message={createAction.error || action.error || ''} />
              </div>
            )}
            <div className="flex items-center gap-2 sm:col-span-2">
              <PrimaryButton onClick={create} disabled={createAction.busy}>
                {createAction.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Create
              </PrimaryButton>
              <GhostButton onClick={() => setShowForm(false)}>Discard</GhostButton>
            </div>
          </div>
        </section>
      )}

      {action.error && !showForm && (
        <ErrorBanner title="Action failed" message={action.error} className="mb-4" />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section className="overflow-hidden rounded-panel border border-line bg-card">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3">
            <span className="text-txmuted">
              <PhoneCall className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-tx">Requests</h2>
          </div>
          {state.loading && !callbacks.length ? (
            <LoadingBlock label="Loading callbacks…" />
          ) : state.error ? (
            <div className="p-4">
              <ErrorBanner title="Could not load" message={state.error} />
            </div>
          ) : callbacks.length === 0 ? (
            <EmptyState title="No callbacks" description="Nothing matches this filter." />
          ) : (
            <ul className="max-h-[70vh] divide-y divide-linesoft overflow-y-auto">
              {callbacks.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(item.id)}
                    className={cn(
                      'w-full px-4 py-3 text-left transition',
                      item.id === selected ? 'bg-accsoft' : 'hover:bg-paper',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-tx">
                        {item.customer_name || item.wa_id}
                      </span>
                      <span className="shrink-0 text-[11px] text-txfaint">{relativeTime(item.created_at)}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-txmuted">{item.wa_id}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Pill tone={STATUS_TONE[item.status] ?? 'neutral'}>{item.status.replace(/_/g, ' ')}</Pill>
                      <Pill tone={PRIORITY_TONE[item.priority] ?? 'neutral'}>{item.priority}</Pill>
                      {item.meet_link && (
                        <Pill tone="ok">
                          <span className="inline-flex items-center gap-1">
                            <Video className="h-3 w-3" />
                            meet
                          </span>
                        </Pill>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex min-h-[24rem] flex-col rounded-panel border border-line bg-card">
          {!active ? (
            <EmptyState
              className="flex-1"
              title="Pick a request"
              description="Select a callback to read it and act on it."
            />
          ) : (
            <>
              <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3 sm:px-5">
                <span className="text-txmuted">
                  <PhoneCall className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-tx">{active.customer_name || active.wa_id}</h2>
                  <p className="text-xs text-txmuted">
                    {active.wa_id} · raised {formatDate(active.created_at)}
                  </p>
                </div>
              </div>
              <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone={STATUS_TONE[active.status] ?? 'neutral'}>{active.status.replace(/_/g, ' ')}</Pill>
                  <Pill tone={PRIORITY_TONE[active.priority] ?? 'neutral'}>{active.priority} priority</Pill>
                  <Pill>{active.callback_type.replace(/_/g, ' ')}</Pill>
                  {active.assigned_agent_name && <Pill tone="ok">{active.assigned_agent_name}</Pill>}
                </div>

                <div className="rounded-panel bg-paper p-3.5 text-sm leading-relaxed text-tx">
                  {active.purpose || active.additional_info || 'No purpose recorded.'}
                </div>

                <div className="grid gap-2 text-xs text-txmuted sm:grid-cols-2">
                  <p>
                    Preferred:{' '}
                    <span className="text-tx">
                      {[active.preferred_date, active.preferred_time].filter(Boolean).join(' ') || 'any time'}
                    </span>
                  </p>
                  <p>
                    Phone: <span className="text-tx">{active.customer_phone || '—'}</span>
                  </p>
                  <p>
                    Scheduled:{' '}
                    <span className="text-tx">
                      {active.scheduled_start_time ? formatDate(active.scheduled_start_time) : 'not yet'}
                    </span>
                  </p>
                  <p>
                    Updated: <span className="text-tx">{relativeTime(active.updated_at)}</span>
                  </p>
                </div>

                {active.meet_link && (
                  <a
                    href={active.meet_link}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 text-sm font-medium text-acc hover:underline"
                  >
                    <Video className="h-4 w-4" />
                    Open Google Meet
                  </a>
                )}

                {OPEN.has(active.status) && (
                  <div className="space-y-3 rounded-panel bg-paper p-3.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-txmuted">
                      Schedule with Google Meet
                    </p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <FieldInput
                        label="Agent name"
                        value={schedule.agent_name}
                        onChange={(e) => setSchedule((s) => ({ ...s, agent_name: e.target.value }))}
                        placeholder="Agent handling the call (defaults to Support Agent)"
                      />
                      <FieldInput
                        label="Agent ID (optional)"
                        value={schedule.agent_id}
                        onChange={(e) => setSchedule((s) => ({ ...s, agent_id: e.target.value }))}
                        placeholder="Defaults to the name"
                      />
                      <FieldInput
                        label="Start"
                        type="datetime-local"
                        value={schedule.start}
                        onChange={(e) => setSchedule((s) => ({ ...s, start: e.target.value }))}
                      />
                      <FieldInput
                        label="End"
                        type="datetime-local"
                        value={schedule.end}
                        onChange={(e) => setSchedule((s) => ({ ...s, end: e.target.value }))}
                      />
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <PrimaryButton onClick={book} disabled={action.busy}>
                        {action.busy ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <CalendarCheck className="h-4 w-4" />
                        )}
                        Schedule
                      </PrimaryButton>
                      <DangerButton onClick={cancel} disabled={action.busy}>
                        <XCircle className="h-4 w-4" />
                        Cancel request
                      </DangerButton>
                    </div>
                  </div>
                )}

                {OPEN.has(active.status) && (
                  <div className="space-y-3 rounded-panel bg-paper p-3.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-txmuted">Complete the call</p>
                    <FieldTextarea
                      label="Meeting notes"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Outcome of the call…"
                    />
                    <PrimaryButton onClick={complete} disabled={action.busy}>
                      {action.busy ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      Mark completed
                    </PrimaryButton>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
