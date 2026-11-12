import { useState } from 'react'
import { AlertTriangle, Loader2, MessageSquareWarning, Send, Trash2 } from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import { COMPLAINT_PRIORITIES, COMPLAINT_STATUSES, conversationsApi, stripRoute } from '@/lib/conversations'
import { formatDate, relativeTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import {
  Check,
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  FieldTextarea,
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

const FILTERS = ['all', ...COMPLAINT_STATUSES]

const STATUS_TONE: Record<string, PillTone> = {
  open: 'bad',
  in_progress: 'warn',
  awaiting_info: 'ok',
  resolved: 'ok',
  closed: 'neutral',
}

const PRIORITY_TONE: Record<string, PillTone> = {
  low: 'neutral',
  normal: 'neutral',
  high: 'warn',
  urgent: 'bad',
}

/**
 * Complaints raised through the bot. Reviewing is open to anyone with
 * `view_complaints`; changing status, replying or deleting needs
 * `manage_complaints`.
 */
export function ComplaintsPanel({ tenantId }: { tenantId: string }) {
  const { can } = useAuth()
  const toast = useToast()
  const confirmDialog = useConfirm()
  const action = useAction()

  const [status, setStatus] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const canManage = can('manage_complaints')

  const state = useAsync(
    (signal) => conversationsApi.complaints(tenantId, { status: status === 'all' ? undefined : status }, signal),
    [tenantId, status],
  )

  const complaints = state.data?.complaints ?? []
  const active = complaints.find((c) => c.ticket_id === selected) ?? null
  const openLoaded = complaints.filter((c) => c.status === 'open').length

  const refresh = () => state.reload()

  const patch = async (body: { status?: string; priority?: string; assigned_to?: string }) => {
    if (!active) return
    const result = await action.run(() => conversationsApi.updateComplaint(tenantId, active.ticket_id, body))
    if (result) {
      toast.push('Complaint updated')
      refresh()
    }
  }

  const remove = async () => {
    if (!active) return
    if (
      !(await confirmDialog({
        message: `Delete complaint ${active.ticket_id}? This cannot be undone.`,
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return
    const result = await action.run(() => conversationsApi.deleteComplaint(tenantId, active.ticket_id))
    if (result) {
      toast.push('Complaint deleted')
      setSelected(null)
      refresh()
    }
  }

  const reply = async (message: string, resolve: boolean) => {
    if (!active) return false
    const result = await action.run(() =>
      conversationsApi.replyComplaint(tenantId, active.ticket_id, message, resolve ? 'resolved' : undefined),
    )
    if (result) {
      toast.push('Reply sent')
      refresh()
      return true
    }
    return false
  }

  return (
    <div>
      <PageHeader
        title="Help requests"
        description="Issues customers raised through the bot. Reply to reopen the conversation, or move the ticket through to resolved."
        meta={
          state.data && (
            <>
              <Pill tone={state.data.open_count ? 'bad' : 'ok'}>
                {state.data.open_count} open
              </Pill>
              <Pill>{state.data.count} shown</Pill>
            </>
          )
        }
      />

      {openLoaded > 0 && (
        <Panel title="Needs you" className="mb-4">
          <NeedRow icon={<AlertTriangle className="h-4 w-4" />}>
            <strong>{openLoaded}</strong> {openLoaded === 1 ? 'complaint is' : 'complaints are'} still open in the
            list below — open one to reply or move it forward.
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

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section className="overflow-hidden rounded-panel border border-line bg-card">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3">
            <span className="text-txmuted">
              <MessageSquareWarning className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-tx">Tickets</h2>
          </div>
          {state.loading && !complaints.length ? (
            <LoadingBlock label="Loading complaints…" />
          ) : state.error ? (
            <div className="p-4">
              <ErrorBanner title="Could not load" message={state.error} />
            </div>
          ) : complaints.length === 0 ? (
            <EmptyState title="No complaints" description="Nothing matches this filter." />
          ) : (
            <ul className="max-h-[70vh] divide-y divide-linesoft overflow-y-auto">
              {complaints.map((item) => (
                <li key={item.ticket_id}>
                  <button
                    type="button"
                    onClick={() => setSelected(item.ticket_id)}
                    className={cn(
                      'w-full px-4 py-3 text-left transition',
                      item.ticket_id === selected ? 'bg-accsoft' : 'hover:bg-paper',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-tx">
                        {item.subject || item.complaint_type || item.ticket_id}
                      </span>
                      <span className="shrink-0 text-[11px] text-txfaint">{relativeTime(item.created_at)}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-txmuted">{item.ticket_id}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Pill tone={STATUS_TONE[item.status] ?? 'neutral'}>{item.status.replace(/_/g, ' ')}</Pill>
                      <Pill tone={PRIORITY_TONE[item.priority] ?? 'neutral'}>{item.priority}</Pill>
                      {item.assigned_to && <Pill tone="ok">{item.assigned_to}</Pill>}
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
              title="Pick a ticket"
              description="Select a complaint to read it and act on it."
            />
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-linesoft px-4 py-3 sm:px-5">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="text-txmuted">
                    <AlertTriangle className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="truncate text-sm font-semibold text-tx">
                      {active.subject || active.complaint_type || active.ticket_id}
                    </h2>
                    <p className="text-xs text-txmuted">
                      {active.ticket_id} · {active.wa_id || 'no customer'} · raised {formatDate(active.created_at)}
                    </p>
                  </div>
                </div>
                {canManage && (
                  <DangerButton onClick={remove} disabled={action.busy} className="px-3 py-1.5 text-xs">
                    {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    Delete
                  </DangerButton>
                )}
              </div>

              <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone={STATUS_TONE[active.status] ?? 'neutral'}>{active.status.replace(/_/g, ' ')}</Pill>
                  <Pill tone={PRIORITY_TONE[active.priority] ?? 'neutral'}>{active.priority} priority</Pill>
                  {active.complaint_type && <Pill>{active.complaint_type}</Pill>}
                </div>

                <div className="rounded-panel bg-paper p-3.5 text-sm leading-relaxed text-tx">
                  {active.description || 'No description recorded.'}
                </div>

                {active.last_user_message && (
                  <p className="text-xs text-txmuted">
                    Last from customer: {stripRoute(active.last_user_message)}
                  </p>
                )}

                {canManage && (
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="w-44">
                      <FieldSelect
                        label="Status"
                        value={active.status}
                        onChange={(e) => patch({ status: e.target.value })}
                      >
                        {COMPLAINT_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s.replace(/_/g, ' ')}
                          </option>
                        ))}
                      </FieldSelect>
                    </div>
                    <div className="w-40">
                      <FieldSelect
                        label="Priority"
                        value={active.priority}
                        onChange={(e) => patch({ priority: e.target.value })}
                      >
                        {COMPLAINT_PRIORITIES.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </FieldSelect>
                    </div>
                    <div className="w-44">
                      <FieldInput
                        label="Assignee"
                        defaultValue={active.assigned_to ?? ''}
                        placeholder="unassigned"
                        onBlur={(e) => {
                          if (e.target.value !== (active.assigned_to ?? '')) patch({ assigned_to: e.target.value })
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {canManage && active.wa_id && (
                <ComplaintReply busy={action.busy} onSend={reply} />
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function ComplaintReply({
  busy,
  onSend,
}: {
  busy: boolean
  onSend: (message: string, resolve: boolean) => Promise<boolean>
}) {
  const [draft, setDraft] = useState('')
  const [resolve, setResolve] = useState(false)

  const send = async () => {
    const message = draft.trim()
    if (!message) return
    if (await onSend(message, resolve)) {
      setDraft('')
      setResolve(false)
    }
  }

  return (
    <div className="border-t border-linesoft p-4">
      <FieldTextarea
        rows={2}
        value={draft}
        placeholder="Reply to the customer…"
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <Check checked={resolve} onChange={setResolve} label="Mark resolved after sending" />
        <PrimaryButton onClick={send} disabled={!draft.trim() || busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Send reply
        </PrimaryButton>
      </div>
    </div>
  )
}
