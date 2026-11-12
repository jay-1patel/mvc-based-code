import { useState } from 'react'
import { History, Loader2, Rocket, Undo2 } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { tenantsApi } from '@/lib/tenants'
import { formatDate, isTruthyFlag, relativeTime } from '@/lib/format'
import {
  DangerButton,
  ErrorBanner,
  GhostButton,
  Panel,
  Pill,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useAuth } from '@/lib/auth'
import { useTenantDetail, useVersions } from './hooks'

export function VersionsPanel({ tenantId }: { tenantId: string }) {
  const versions = useVersions(tenantId)
  const detail = useTenantDetail(tenantId)
  const action = useAction()
  const toast = useToast()
  const { identity } = useAuth()
  // Decided role rule (flipped from the old model): only the tenant admin
  // publishes and rolls back. A super admin is view-only here; sub admins
  // get neither action.
  const isAdmin = identity?.role === 'admin'
  const [confirming, setConfirming] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ version: number; data: Record<string, unknown> } | null>(null)

  const publish = async () => {
    const result = await action.run(() => tenantsApi.publish(tenantId))
    if (result) {
      if (result.status === 'pending_approval') {
        toast.push(result.message ?? 'Change sent for super admin approval.')
      } else {
        toast.push(`Published version ${result.version}`)
      }
      versions.reload()
      detail.reload()
    }
  }

  const rollback = async (version: number) => {
    const result = await action.run(() => tenantsApi.rollback(tenantId, version))
    if (result) {
      toast.push(`Rolled back to v${result.version}`)
      setConfirming(null)
      versions.reload()
      detail.reload()
    }
  }

  const openVersion = async (version: number) => {
    if (preview?.version === version) {
      setPreview(null)
      return
    }
    const record = await action.run(() => tenantsApi.version(tenantId, version))
    if (record) setPreview({ version, data: record })
  }

  const rows = versions.data?.versions ?? []
  const current = versions.data?.current_version ?? 0

  return (
    <div>
      <PageHeader
        title="Versions"
        description="Publishing appends an immutable version and swaps the live profile. Rolling back never deletes anything — it re-points which version is current."
        actions={
          isAdmin ? (
            <PrimaryButton disabled={action.busy} onClick={publish}>
              {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
              Publish draft
            </PrimaryButton>
          ) : undefined
        }
        meta={
          <Pill tone={current ? 'ok' : 'warn'}>
            {current ? `live version v${current}` : 'nothing published yet'}
          </Pill>
        }
      />

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}

      {versions.loading ? (
        <LoadingBlock label="Loading versions…" />
      ) : (
        <Panel title="History" action={<History className="h-4 w-4 text-txmuted" />}>
          <p className="-mt-1 mb-2 text-sm text-txmuted">{rows.length} version(s)</p>
          {!rows.length ? (
            <p className="text-sm text-txmuted">
              No versions yet. Save a draft in the profile editor, then publish it here.
            </p>
          ) : (
            <div className="divide-y divide-linesoft">
              {rows.map((row) => {
                const isCurrent = isTruthyFlag(row.is_current) || row.version === current
                return (
                  <div key={row.version} className="flex flex-wrap items-center gap-3 py-3">
                    <div className="w-14 shrink-0">
                      <span className="font-mono text-sm text-tx">v{row.version}</span>
                    </div>
                    {isCurrent && <Pill tone="ok">current</Pill>}
                    <div className="min-w-0 flex-1 text-sm text-txmuted">
                      <p className="text-tx">{row.note || row.published_by || '—'}</p>
                      <p className="mt-0.5 text-xs text-txfaint">
                        {formatDate(row.created_at)} · {relativeTime(row.created_at)}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <GhostButton onClick={() => openVersion(row.version)}>
                        {preview?.version === row.version ? 'Hide' : 'Inspect'}
                      </GhostButton>
                      {!isCurrent && isAdmin &&
                        (confirming === row.version ? (
                          <>
                            <DangerButton disabled={action.busy} onClick={() => rollback(row.version)}>
                              {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                              Confirm
                            </DangerButton>
                            <GhostButton onClick={() => setConfirming(null)}>Cancel</GhostButton>
                          </>
                        ) : (
                          <GhostButton onClick={() => setConfirming(row.version)}>
                            <Undo2 className="h-3.5 w-3.5" />
                            Roll back
                          </GhostButton>
                        ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Panel>
      )}

      {preview && (
        <Panel title={`Version ${preview.version}`} className="mt-4">
          <p className="-mt-1 mb-3 text-sm text-txmuted">Raw stored payload.</p>
          <pre className="max-h-96 overflow-auto scroll-thin rounded-lg border border-line bg-paper p-4 font-mono text-xs leading-relaxed text-txmuted">
            {JSON.stringify(preview.data, null, 2)}
          </pre>
        </Panel>
      )}
    </div>
  )
}
