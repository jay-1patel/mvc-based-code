import { ClipboardCheck, ShieldCheck, Building2 } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { useTenants } from '@/lib/tenants'
import { useAsync } from '@/lib/hooks'
import { tenantApprovalsApi } from '@/lib/tenant-approvals'
import { apiOnboarding } from '@/lib/api-onboarding'
import { navigate } from '@/lib/router'
import { NeedRow, Panel, PrimaryButton, StatCard } from '@/components/remake/ui'
import { TenantList } from './tenant-list'

function greeting(): string {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

/**
 * Today — the super admin home.
 *
 * Approvals first, because that is the super admin's actual job: business
 * change requests and API requests waiting for a decision, then the
 * businesses themselves (the tenant list, with its data-health flags).
 * Counts come from the same list endpoints the review screens use.
 */
export function SuperToday() {
  const { identity } = useAuth()
  const { tenants } = useTenants()

  const changeRequests = useAsync((s) => tenantApprovalsApi.all('pending', s), [])
  const apiRequests = useAsync((s) => apiOnboarding.all('pending', s), [])

  const pendingChanges = changeRequests.data ?? []
  const pendingApis = apiRequests.data ?? []
  const needingAttention = tenants.filter((t) => !t.current_version || (t.status && t.status !== 'active'))

  const needs: React.ReactNode[] = []
  if (pendingChanges.length) {
    needs.push(
      <NeedRow
        key="changes"
        icon={<ClipboardCheck className="h-4 w-4" />}
        action={<PrimaryButton onClick={() => navigate('/tenant-requests')}>Review</PrimaryButton>}
      >
        <strong>{pendingChanges.length}</strong> business{' '}
        {pendingChanges.length === 1 ? 'request is' : 'requests are'} waiting for your decision
      </NeedRow>,
    )
  }
  if (pendingApis.length) {
    needs.push(
      <NeedRow
        key="apis"
        icon={<ShieldCheck className="h-4 w-4" />}
        action={<PrimaryButton onClick={() => navigate('/api-requests')}>Review</PrimaryButton>}
      >
        <strong>{pendingApis.length}</strong> API access{' '}
        {pendingApis.length === 1 ? 'request is' : 'requests are'} waiting for your decision
      </NeedRow>,
    )
  }
  if (needingAttention.length) {
    needs.push(
      <NeedRow key="health" icon={<Building2 className="h-4 w-4" />}>
        <strong>{needingAttention.length}</strong>{' '}
        {needingAttention.length === 1 ? 'business has' : 'businesses have'} no published version or an
        inactive status — see Data health below
      </NeedRow>,
    )
  }

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-tx">
          {greeting()}, {identity?.username ?? 'there'}
        </h1>
        <p className="mt-1 text-sm text-txmuted">
          Super admin · {tenants.length} businesses on the platform ·{' '}
          {new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
        </p>
      </header>

      <Panel title="Needs you" className="mb-4">
        {changeRequests.loading || apiRequests.loading ? (
          <p className="py-1 text-sm text-txmuted">Checking the approval queues…</p>
        ) : needs.length ? (
          <div>{needs}</div>
        ) : (
          <p className="py-1 text-sm text-txmuted">Nothing needs you right now.</p>
        )}
      </Panel>

      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <StatCard
          value={changeRequests.loading ? '—' : pendingChanges.length}
          label="business requests waiting"
          tone={pendingChanges.length ? 'attention' : 'normal'}
        />
        <StatCard
          value={apiRequests.loading ? '—' : pendingApis.length}
          label="API requests waiting"
          tone={pendingApis.length ? 'attention' : 'normal'}
        />
        <StatCard value={tenants.length} label="businesses on the platform" />
      </div>

      <TenantList />
    </div>
  )
}
