import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useAsync } from '@/lib/hooks'
import { formatDate } from '@/lib/format'
import { Card, CardBody } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, EmptyState, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { RefreshCw } from 'lucide-react'

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
        title="Errors"
        description={
          scopedTenant
            ? 'Recent API and runtime errors for your tenant. Read-only — quote the request id when reporting a problem.'
            : 'Recent API and runtime errors across all tenants. Read-only — quote the request id when reporting a problem.'
        }
        actions={
          <Button variant="secondary" size="sm" icon={<RefreshCw className="h-4 w-4" />} onClick={state.reload} disabled={state.loading}>
            Refresh
          </Button>
        }
      />
      {state.error && <Alert tone="danger" title="Could not load errors">{state.error}</Alert>}
      {state.loading && <LoadingBlock label="Loading errors…" />}
      {!state.loading && !state.error && rows.length === 0 && (
        <Card>
          <CardBody>
            <EmptyState title="No errors recorded" description="Nothing has failed recently — or the error reporter has not persisted anything yet." />
          </CardBody>
        </Card>
      )}
      <div className="space-y-3">
        {rows.map((row) => (
          <Card key={row.id} className="bg-surface-panel border-surface-line">
            <CardBody className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm text-slate-200">
                <span className="font-medium">{row.api || 'Unknown API'}</span>
                {row.provider && <Badge tone="neutral">{row.provider}</Badge>}
                {row.http_status ? <Badge tone="danger">HTTP {row.http_status}</Badge> : null}
                {row.severity && <Badge tone="muted">{row.severity}</Badge>}
                <span className="text-xs text-slate-500">{formatDate(row.created_at)}</span>
              </div>
              {row.message && <p className="text-sm text-slate-300">{row.message}</p>}
              <div className="flex flex-wrap gap-3 text-xs text-slate-500">
                <span>Tenant: {row.tenant_id || 'Platform'}</span>
                {row.request_id && <span>Request: {row.request_id}</span>}
              </div>
              {row.detail && (
                <pre className="max-h-40 overflow-auto scroll-thin rounded-lg bg-surface p-3 font-mono text-xs leading-relaxed text-slate-400">
                  {row.detail}
                </pre>
              )}
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  )
}
