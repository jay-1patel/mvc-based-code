import { useState } from 'react'
import { Copy, KeyRound, Loader2, Plus, ShieldOff } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { tenantsApi, useTenants } from '@/lib/tenants'
import { formatDate, isTruthyFlag } from '@/lib/format'
import {
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  GhostButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useAsync } from '@/lib/hooks'
import { usePermissions } from '@/lib/permissions'

export function TokensPanel({ tenantId }: { tenantId: string }) {
  const { canWriteTenant, isSuperAdmin } = usePermissions()
  const { reload: reloadTenants } = useTenants()
  const toast = useToast()
  const action = useAction()
  const [label, setLabel] = useState('')
  const [minted, setMinted] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [revoking, setRevoking] = useState<number | null>(null)

  const tokens = useAsync(() => tenantsApi.tokens(tenantId), [tenantId])
  // Mint/revoke are tenant-data writes: a super admin is view-only here.
  const canWrite = canWriteTenant('manage_operations')

  const mint = async () => {
    const result = await action.run(() => tenantsApi.mintToken(tenantId, label.trim()))
    if (result) {
      setMinted(result.token)
      setLabel('')
      setCopied(false)
      tokens.reload()
      toast.push('Token minted. Copy it now — the server only keeps a hash.')
    }
  }

  const revoke = async (id: number) => {
    const result = await action.run(() => tenantsApi.revokeToken(tenantId, id))
    if (result) {
      toast.push('Token revoked')
      setRevoking(null)
      tokens.reload()
      reloadTenants()
    }
  }

  const copy = async () => {
    if (!minted) return
    try {
      await navigator.clipboard.writeText(minted)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="API tokens"
        description="A tenant token is scoped to exactly one tenant. It can read and publish that tenant's profile and nothing else — cross-tenant access is refused by the server, not by this UI."
      />

      {!canWrite && (
        <Notice tone="warn" title="Read-only" className="mb-4">
          {isSuperAdmin
            ? 'View-only — tokens are minted and revoked by the tenant’s admin.'
            : 'Minting and revoking tokens needs the manage_operations permission.'}
        </Notice>
      )}
      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}

      {minted && (
        <Notice tone="success" title="New token — shown once" className="mb-4">
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-paper px-2 py-1 font-mono text-xs text-tx">
              {minted}
            </code>
            <GhostButton onClick={copy} className="px-2.5 py-1.5 text-xs">
              <Copy className="h-3.5 w-3.5" />
              {copied ? 'Copied' : 'Copy'}
            </GhostButton>
          </div>
        </Notice>
      )}

      {canWrite && (
        <Panel title="Mint a token" className="mb-4">
          <p className="-mt-1 mb-3 text-sm text-txmuted">
            Use it as a bearer token against this tenant's routes only.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1">
              <FieldInput
                label="Label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="CI publish pipeline"
              />
            </div>
            <PrimaryButton disabled={!canWrite || action.busy} onClick={mint}>
              {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Mint token
            </PrimaryButton>
          </div>
        </Panel>
      )}

      <Panel
        title="Issued tokens"
        action={<KeyRound className="h-4 w-4 text-txmuted" />}
      >
        {tokens.loading ? (
          <LoadingBlock label="Loading tokens…" />
        ) : (tokens.data?.tokens ?? []).length === 0 ? (
          <EmptyState
            title="No tokens for this tenant"
            description="Mint one to let an external system publish this tenant's profile."
          />
        ) : (
          <div className="divide-y divide-linesoft">
            {tokens.data!.tokens.map((token) => {
              const revoked = isTruthyFlag(token.revoked_at)
              return (
                <div key={token.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-tx">{token.label || `token #${token.id}`}</span>
                      {revoked ? <Pill tone="bad">revoked</Pill> : <Pill tone="ok">active</Pill>}
                    </div>
                    <p className="mt-1 text-xs text-txfaint">
                      created {formatDate(token.created_at)}
                      {token.created_by ? ` by ${token.created_by}` : ''}
                      {token.last_used_at ? ` · last used ${formatDate(token.last_used_at)}` : ''}
                    </p>
                  </div>
                  {!revoked &&
                    (revoking === token.id ? (
                      <>
                        <DangerButton disabled={action.busy} onClick={() => revoke(token.id)}>
                          {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                          Confirm
                        </DangerButton>
                        <GhostButton onClick={() => setRevoking(null)}>Cancel</GhostButton>
                      </>
                    ) : (
                      <GhostButton
                        disabled={!canWrite}
                        onClick={() => setRevoking(token.id)}
                      >
                        <ShieldOff className="h-3.5 w-3.5" />
                        Revoke
                      </GhostButton>
                    ))}
                </div>
              )
            })}
          </div>
        )}
      </Panel>
    </div>
  )
}
