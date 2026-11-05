import { useState } from 'react'
import { Copy, KeyRound, Plus, ShieldOff } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { tenantsApi, useTenants } from '@/lib/tenants'
import { formatDate, isTruthyFlag } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Alert, EmptyState, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'

export function TokensPanel({ tenantId }: { tenantId: string }) {
  const { can } = useAuth()
  const { reload: reloadTenants } = useTenants()
  const toast = useToast()
  const action = useAction()
  const [label, setLabel] = useState('')
  const [minted, setMinted] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [revoking, setRevoking] = useState<number | null>(null)

  const tokens = useAsync(() => tenantsApi.tokens(tenantId), [tenantId])
  const adminOnly = can('manage_operations')

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

      {!adminOnly && (
        <Alert tone="warning" title="Read-only" className="mb-4">
          Minting and revoking tokens needs the manage_operations permission.
        </Alert>
      )}
      {action.error && (
        <Alert tone="danger" title="Action failed" className="mb-4">
          {action.error}
        </Alert>
      )}

      {minted && (
        <Alert tone="success" title="New token — shown once" className="mb-4">
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-surface-raised px-2 py-1 font-mono text-xs">
              {minted}
            </code>
            <Button size="sm" variant="secondary" onClick={copy} icon={<Copy className="h-3.5 w-3.5" />}>
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </Alert>
      )}

      <Card className="mb-4">
        <CardHeader title="Mint a token" description="Use it as a bearer token against this tenant's routes only." />
        <CardBody>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1">
              <Input
                label="Label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="CI publish pipeline"
              />
            </div>
            <Button
              variant="primary"
              loading={action.busy}
              disabled={!adminOnly}
              onClick={mint}
              icon={<Plus className="h-4 w-4" />}
            >
              Mint token
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Issued tokens" icon={<KeyRound className="h-4 w-4" />} />
        {tokens.loading ? (
          <LoadingBlock label="Loading tokens…" />
        ) : (tokens.data?.tokens ?? []).length === 0 ? (
          <EmptyState title="No tokens for this tenant" description="Mint one to let an external system publish this tenant's profile." />
        ) : (
          <CardBody className="divide-y divide-surface-line">
            {tokens.data!.tokens.map((token) => {
              const revoked = isTruthyFlag(token.revoked_at)
              return (
                <div key={token.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-slate-100">{token.label || `token #${token.id}`}</span>
                      {revoked ? <Badge tone="danger">revoked</Badge> : <Badge tone="success">active</Badge>}
                    </div>
                    <p className="mt-1 text-xs text-slate-600">
                      created {formatDate(token.created_at)}
                      {token.created_by ? ` by ${token.created_by}` : ''}
                      {token.last_used_at ? ` · last used ${formatDate(token.last_used_at)}` : ''}
                    </p>
                  </div>
                  {!revoked &&
                    (revoking === token.id ? (
                      <>
                        <Button size="sm" variant="danger" loading={action.busy} onClick={() => revoke(token.id)}>
                          Confirm
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setRevoking(null)}>
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={!adminOnly}
                        icon={<ShieldOff className="h-3.5 w-3.5" />}
                        onClick={() => setRevoking(token.id)}
                      >
                        Revoke
                      </Button>
                    ))}
                </div>
              )
            })}
          </CardBody>
        )}
      </Card>
    </div>
  )
}
