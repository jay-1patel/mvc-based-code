import { useState } from 'react'
import { Check, Loader2, X } from 'lucide-react'
import { apiOnboarding, type ApiOnboardingRequest, type ApiOnboardingStatus } from '@/lib/api-onboarding'
import { useAsync, useAction } from '@/lib/hooks'
import { formatDate } from '@/lib/format'
import {
  Card,
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldSelect,
  FieldTextarea,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  type PillTone,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

const LABELS = { payment_api: 'Payment Gateway API', order_api: 'Order & Shipping API' } as const
const TONES: Record<ApiOnboardingStatus, PillTone> = {
  pending: 'warn',
  approved: 'ok',
  rejected: 'bad',
}

export function ApiOnboardingReview() {
  const [filter, setFilter] = useState<ApiOnboardingStatus | 'all'>('pending')
  const state = useAsync((signal) => apiOnboarding.all(filter === 'all' ? undefined : filter, signal), [filter])
  const action = useAction()
  const toast = useToast()
  const [notes, setNotes] = useState<Record<number, string>>({})

  const decide = async (request: ApiOnboardingRequest, decision: 'approved' | 'rejected') => {
    const result = await action.run(() => apiOnboarding.decide(request.id, decision, notes[request.id] ?? ''))
    if (result) {
      toast.push(`${LABELS[request.api_type]} request ${decision}`)
      setNotes((current) => ({ ...current, [request.id]: '' }))
      state.reload()
    }
  }

  return (
    <div>
      <PageHeader
        title="API requests"
        description="Review tenant requests for external Payment API and Order API access. Approval grants eligibility only; it does not configure credentials or enable live traffic."
        meta={<Pill tone="ok">Superadmin review</Pill>}
      />

      <Notice tone="info" title="Approval is not live activation" className="mb-4">
        Approved requests still require secure credential provisioning before any API connection can be enabled. Never ask tenants to send API secrets through this queue.
      </Notice>

      <div className="mb-4 max-w-xs">
        <FieldSelect label="Request status" value={filter} onChange={(event) => setFilter(event.target.value as ApiOnboardingStatus | 'all')}>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="all">All requests</option>
        </FieldSelect>
      </div>

      {action.error && <ErrorBanner title="Could not update request" message={action.error} className="mb-4" />}
      {state.loading && <LoadingBlock label="Loading API requests…" />}
      {state.error && <ErrorBanner title="Could not load API requests" message={state.error} />}
      {state.data?.length === 0 && !state.loading && (
        <Card><EmptyState title="No requests" description="There are no API access requests in this status." /></Card>
      )}
      {state.data && state.data.length > 0 && (
        <div className="space-y-3">
          {state.data.map((request: ApiOnboardingRequest) => (
            <Panel
              key={request.id}
              title={`${LABELS[request.api_type as keyof typeof LABELS]} · ${request.tenant_id}`}
              action={<Pill tone={TONES[request.status as ApiOnboardingStatus]}>{request.status}</Pill>}
            >
              <p className="-mt-1 mb-4 text-sm text-txmuted">
                Submitted by {request.requester_username} on {formatDate(request.created_at)}
                {request.provider ? ` · ${request.provider}` : ''} · {request.environment}
              </p>
              <div className="space-y-4">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-tx">{request.purpose}</p>
                {request.status === 'pending' ? (
                  <>
                    <FieldTextarea
                      label="Decision note (optional)"
                      rows={2}
                      maxLength={2000}
                      value={notes[request.id] ?? ''}
                      onChange={(event) => setNotes((current) => ({ ...current, [request.id]: event.target.value }))}
                      hint="Visible to the tenant admin. Do not include credentials or secrets."
                    />
                    <div className="flex flex-wrap gap-2">
                      <PrimaryButton disabled={action.busy} onClick={() => decide(request, 'approved')}>
                        {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        Approve eligibility
                      </PrimaryButton>
                      <DangerButton disabled={action.busy} onClick={() => decide(request, 'rejected')}>
                        {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
                        Reject request
                      </DangerButton>
                    </div>
                  </>
                ) : (
                  <div className="rounded-lg border border-line bg-paper p-3 text-xs text-txmuted">
                    Reviewed by {request.reviewer_username || 'superadmin'}{request.decided_at ? ` on ${formatDate(request.decided_at)}` : ''}
                    {request.decision_note && <p className="mt-1 whitespace-pre-wrap text-tx">{request.decision_note}</p>}
                    {request.status === 'approved' && <p className="mt-2 text-ok">{request.eligible ? 'Eligibility granted.' : 'Eligibility was not recorded; contact engineering.'} Secure setup is still required before live API use.</p>}
                  </div>
                )}
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  )
}
