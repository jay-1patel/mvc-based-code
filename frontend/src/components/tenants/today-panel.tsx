import { useMemo } from 'react'
import { AlertTriangle, Globe, MessagesSquare, Send } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { useTenants } from '@/lib/tenants'
import { usePermissions } from '@/lib/permissions'
import { useAsync } from '@/lib/hooks'
import { api } from '@/lib/api'
import { conversationsApi } from '@/lib/conversations'
import { crawlApi } from '@/lib/crawl'
import { operationsApi } from '@/lib/operations'
import { tenantApprovalsApi } from '@/lib/tenant-approvals'
import { navigate } from '@/lib/router'
import { GhostButton, NeedRow, Panel, PrimaryButton, SourceChip, StatCard } from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { useTenantDetail } from './hooks'

interface ErrorRow {
  created_at?: string
  message?: string
  detail?: string
  api?: string
  provider?: string
}

/**
 * A send failure, as far as the error log can tell: the Phase-1 error
 * reporter persists API/runtime errors (including failed inbox replies),
 * but there is no dedicated failed-send counter — so Today counts log
 * rows from the last hour whose text mentions sending. Rows that are
 * merely "some API failed" do not inflate this number.
 */
function isSendFailure(row: ErrorRow): boolean {
  const text = `${row.api ?? ''} ${row.provider ?? ''} ${row.message ?? ''} ${row.detail ?? ''}`.toLowerCase()
  return text.includes('send') || text.includes('/reply') || text.includes('whatsapp')
}

function greeting(): string {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

const ROLE_LABELS: Record<string, string> = {
  super_admin: 'Super admin',
  admin: 'Admin',
  sub_admin: 'Sub admin',
}

/**
 * Today — the per-role home for a tenant's admins.
 *
 * Order is the design: what needs a person first, then the day's numbers.
 * Every figure comes from an API the console already calls (inbox,
 * chat history, crawl runs, change requests, the error log); anything a
 * call cannot supply is simply left out rather than estimated.
 */
export function TodayPanel({ tenantId }: { tenantId: string }) {
  const { identity } = useAuth()
  const { isSuperAdmin } = usePermissions()
  const { tenants } = useTenants()
  const tenant = tenants.find((t) => t.id === tenantId) ?? null

  const inbox = useAsync((s) => conversationsApi.inbox(tenantId, false, s), [tenantId])
  const history = useAsync(
    (s) => conversationsApi.history(tenantId, { days: 1, limit: 500 }, s),
    [tenantId],
  )
  const runs = useAsync((s) => crawlApi.listRuns(tenantId, s), [tenantId])
  const detail = useTenantDetail(tenantId)
  const mine = useAsync(
    (s) => (isSuperAdmin ? Promise.resolve([]) : tenantApprovalsApi.mine(s)),
    [isSuperAdmin],
  )
  const errors = useAsync(
    (s) =>
      api.get<{ count: number; errors: ErrorRow[] }>(
        `/api/admin/errors?limit=100&tenant_id=${encodeURIComponent(tenantId)}`,
        s,
      ),
    [tenantId],
  )
  const customers = useAsync(
    (s) => operationsApi.tenantCustomers(tenantId, { limit: 500 }, s).then((r) => r.customers),
    [tenantId],
  )

  // The latest finished crawl may be sitting staged, waiting for approval.
  const latestRun = runs.data?.history?.[0] ?? runs.data?.latest ?? null
  const staged = useAsync(
    (s) =>
      latestRun && latestRun.status === 'done'
        ? crawlApi.staged(tenantId, latestRun.id, s)
        : Promise.resolve(null),
    [latestRun?.id, latestRun?.status],
  )

  const waiting = useMemo(() => {
    const q = inbox.data
    if (!q) return null
    return q.handoff_count ?? q.queue.filter((i) => i.handover_mode === 'human').length
  }, [inbox.data])

  const turns = history.data ?? []
  const botAnswered = turns.filter((t) => t.response && t.route !== 'agent').length
  const totalTurns = turns.filter((t) => t.response).length
  const botShare = totalTurns ? Math.round((botAnswered / totalTurns) * 100) : null

  const failedSends = useMemo(() => {
    if (!errors.data) return null
    const hourAgo = Date.now() - 3_600_000
    return errors.data.errors.filter(
      (r) => isSendFailure(r) && (!r.created_at || new Date(r.created_at).getTime() >= hourAgo),
    ).length
  }, [errors.data])

  const stagedWaiting = staged.data?.status === 'staged' ? staged.data : null
  const stagedKb = stagedWaiting?.payload?.bifurcation?.knowledge_articles?.length ?? 0
  const stagedFaqs =
    stagedWaiting?.payload?.bifurcation?.faqs?.length ?? stagedWaiting?.summary?.faqs_count ?? 0

  const pendingMine = (mine.data ?? []).filter((r) => r.status === 'pending').length

  const todayStr = new Date().toDateString()
  const newCustomers = (customers.data ?? []).filter(
    (c) => c.first_seen && new Date(c.first_seen).toDateString() === todayStr,
  ).length

  const needs: React.ReactNode[] = []
  if (waiting) {
    needs.push(
      <NeedRow
        key="chats"
        icon={<MessagesSquare className="h-4 w-4" />}
        action={<PrimaryButton onClick={() => navigate(`/tenants/${tenantId}/inbox`)}>Open</PrimaryButton>}
      >
        <strong>{waiting}</strong> {waiting === 1 ? 'chat is' : 'chats are'} waiting for a human reply
      </NeedRow>,
    )
  }
  if (stagedWaiting) {
    needs.push(
      <NeedRow
        key="crawl"
        icon={<Globe className="h-4 w-4" />}
        action={
          <span className="flex items-center gap-2">
            <SourceChip source="web" />
            <PrimaryButton onClick={() => navigate(`/tenants/${tenantId}/website-knowledge`)}>
              Review
            </PrimaryButton>
          </span>
        }
      >
        Website crawl finished — {stagedKb} knowledge items and {stagedFaqs} FAQs waiting for approval
      </NeedRow>,
    )
  }
  if (failedSends) {
    needs.push(
      <NeedRow
        key="failed"
        icon={<AlertTriangle className="h-4 w-4" />}
        action={<GhostButton onClick={() => navigate('/errors')}>Open</GhostButton>}
      >
        <strong>{failedSends}</strong> {failedSends === 1 ? 'send' : 'sends'} failed in the last hour — see
        Errors &amp; health
      </NeedRow>,
    )
  }
  if (pendingMine) {
    needs.push(
      <NeedRow
        key="requests"
        icon={<Send className="h-4 w-4" />}
        action={<GhostButton onClick={() => navigate('/tenant-requests')}>Open</GhostButton>}
      >
        <strong>{pendingMine}</strong> of your change requests {pendingMine === 1 ? 'is' : 'are'} waiting for
        super admin approval
      </NeedRow>,
    )
  }
  if (detail.data?.has_draft) {
    needs.push(
      <NeedRow
        key="draft"
        icon={<Send className="h-4 w-4" />}
        action={
          <GhostButton onClick={() => navigate(`/tenants/${tenantId}/versions`)}>Review</GhostButton>
        }
      >
        A menu/profile draft is in progress and has not been published
      </NeedRow>,
    )
  }

  const loadingCore = inbox.loading && waiting === null

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-tx">
          {greeting()}, {identity?.username ?? 'there'}
        </h1>
        <p className="mt-1 text-sm text-txmuted">
          {tenant?.display_name || tenantId} · {ROLE_LABELS[identity?.role ?? ''] ?? identity?.role} ·{' '}
          {new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
        </p>
      </header>

      <Panel title="Needs you" className="mb-4">
        {loadingCore ? (
          <LoadingBlock label="Checking what needs you…" />
        ) : needs.length ? (
          <div>{needs}</div>
        ) : (
          <p className="py-1 text-sm text-txmuted">Nothing needs you right now.</p>
        )}
      </Panel>

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <StatCard
          value={waiting ?? '—'}
          label="chats waiting for a human reply"
          tone={waiting ? 'attention' : 'normal'}
        />
        <StatCard
          value={history.loading ? '—' : botAnswered}
          label="customer messages answered by the bot today"
        />
        {failedSends !== null && (
          <StatCard
            value={failedSends}
            label="sends failed in the last hour"
            tone={failedSends > 0 ? 'failure' : 'normal'}
          />
        )}
      </div>

      <Panel title="Today so far">
        <div>
          <NeedRow>
            <span className="flex items-center justify-between">
              <span>New customers</span>
              <strong>{customers.loading ? '—' : newCustomers}</strong>
            </span>
          </NeedRow>
          <NeedRow>
            <span className="flex items-center justify-between">
              <span>Bot answered on its own</span>
              <strong>{botShare === null ? '—' : `${botShare}%`}</strong>
            </span>
          </NeedRow>
          <NeedRow>
            <span className="flex items-center justify-between">
              <span>Conversations in the inbox</span>
              <strong>{inbox.data?.count ?? inbox.data?.queue.length ?? '—'}</strong>
            </span>
          </NeedRow>
        </div>
      </Panel>
    </div>
  )
}
