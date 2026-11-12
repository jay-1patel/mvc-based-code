import { useState } from 'react'
import { Check, Copy, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useAsync } from '@/lib/hooks'
import { formatDate } from '@/lib/format'
import { Card, EmptyState, ErrorBanner, GhostButton, Pill } from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

interface ApiErrorRow {
  id: number
  created_at?: string
  provider?: string
  api?: string
  severity?: string
  message?: string
  detail?: string
  tenant_id?: string | null
  request_id?: string | null
  http_status?: number | null
}

/** The request id, with a one-click copy — this is the id support quotes. */
function RequestId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable — the id stays visible to copy by hand */
    }
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      Request: <code className="font-mono text-tx">{value}</code>
      <button
        type="button"
        onClick={copy}
        className="inline-flex items-center gap-1 rounded-md border border-line bg-card px-1.5 py-0.5 font-medium text-tx transition hover:bg-paper"
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </span>
  )
}

/**
 * Read-only view of the persisted API/runtime errors (Phase 1 error
 * reporter). Super admins see every tenant; a tenant admin is scoped to
 * their own tenant — the server forces that scope, the tenant filter here
 * just makes it explicit. There are no actions on this page by design.
 */
export function ErrorsPanel() {
  const { identity } = useAuth()
  const scopedTenant = identity?.role === 'admin' ? identity.tenant_id ?? null : null
  const state = useAsync(
    (signal) =>
      api.get<{ count: number; errors: ApiErrorRow[] }>(
        `/api/admin/errors?limit=100${scopedTenant ? `&tenant_id=${encodeURIComponent(scopedTenant)}` : ''}`,
        signal,
      ),
    [scopedTenant],
  )
  const rows = state.data?.errors ?? []

  return (
    <div className="space-y-4">
      <PageHeader
        title="Errors & health"
        description={
          scopedTenant
            ? 'Recent API and runtime errors for your tenant. Read-only — quote the request id when reporting a problem.'
            : 'Recent API and runtime errors across all tenants. Read-only — quote the request id when reporting a problem.'
        }
        actions={
          <GhostButton onClick={state.reload} disabled={state.loading}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </GhostButton>
        }
      />
      {state.error && <ErrorBanner title="Could not load errors" message={state.error} />}
      {state.loading && <LoadingBlock label="Loading errors…" />}
      {!state.loading && !state.error && rows.length === 0 && (
        <Card>
          <EmptyState
            title="No errors recorded"
            description="Nothing has failed recently — or the error reporter has not persisted anything yet."
          />
        </Card>
      )}
      <div className="space-y-3">
        {rows.map((row) => (
          <Card key={row.id} className="p-4 sm:p-5">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm text-tx">
                <span className="font-medium">{row.api || 'Unknown API'}</span>
                {row.provider && <Pill>{row.provider}</Pill>}
                {row.http_status ? <Pill tone="bad">HTTP {row.http_status}</Pill> : null}
                {row.severity && <Pill>{row.severity}</Pill>}
                <span className="text-xs text-txfaint">{formatDate(row.created_at)}</span>
              </div>
              {row.message && <p className="text-sm text-tx">{row.message}</p>}
              <div className="flex flex-wrap items-center gap-3 text-xs text-txmuted">
                <span>Tenant: {row.tenant_id || 'Platform'}</span>
                {row.request_id && <RequestId value={row.request_id} />}
              </div>
              {row.detail && (
                <pre className="max-h-40 overflow-auto scroll-thin rounded-lg border border-line bg-paper p-3 font-mono text-xs leading-relaxed text-txmuted">
                  {row.detail}
                </pre>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
