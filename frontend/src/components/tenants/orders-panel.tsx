import { useState } from 'react'
import { useAsync } from '@/lib/hooks'
import { ClipboardList, Search } from 'lucide-react'
import { ORDER_STATUSES, money, operationsApi } from '@/lib/operations'
import { formatDate } from '@/lib/format'
import {
  Card,
  EmptyState,
  ErrorBanner,
  FieldSelect,
  NeedRow,
  Panel,
  Pill,
  type PillTone,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

const STATUS_TONE: Record<string, PillTone> = {
  placed: 'neutral',
  confirmed: 'ok',
  processing: 'ok',
  shipped: 'warn',
  delivered: 'ok',
  completed: 'ok',
  cancelled: 'bad',
  returned: 'bad',
}

const PAYMENT_TONE: Record<string, PillTone> = {
  pending: 'warn',
  paid: 'ok',
  failed: 'bad',
  refunded: 'neutral',
  cod: 'neutral',
}

/**
 * Orders placed through the bot. Read-only; seeing it needs
 * `view_orders` — status changes stay on the backend order endpoints.
 */
export function OrdersPanel({ tenantId }: { tenantId: string }) {
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')

  const state = useAsync(
    (signal) =>
      operationsApi.tenantOrders(tenantId, { status: status === 'all' ? undefined : status, q: search || undefined }, signal),
    [tenantId, status, search],
  )

  const orders = state.data?.orders ?? []
  const counts = state.data?.counts

  return (
    <div>
      <PageHeader
        title="Orders"
        description="Every order the bot has taken, newest first. Filter by status or search by order number, customer or phone."
        meta={
          counts && (
            <>
              <Pill>{counts.total} total</Pill>
              <Pill tone="warn">{counts.placed} placed</Pill>
              <Pill tone="ok">{counts.delivered} delivered</Pill>
              <Pill tone="ok">{money(counts.revenue)} revenue</Pill>
            </>
          )
        }
      />

      {counts && counts.placed > 0 && (
        <Panel title="Needs you" className="mb-4">
          <NeedRow icon={<ClipboardList className="h-4 w-4" />}>
            <strong>{counts.placed}</strong> {counts.placed === 1 ? 'order is' : 'orders are'} placed and
            waiting to be confirmed — filter by the “placed” status below to see them.
          </NeedRow>
        </Panel>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <div className="w-44">
          <FieldSelect label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {['all', ...ORDER_STATUSES].map((filter) => (
              <option key={filter} value={filter}>
                {filter === 'all' ? 'All statuses' : filter.replace('_', ' ')}
              </option>
            ))}
          </FieldSelect>
        </div>
        <div className="min-w-0 max-w-sm flex-1">
          <label className="mb-1.5 block text-sm font-medium text-tx">Search</label>
          <label className="flex items-center gap-2 rounded-lg border border-line bg-inputbg px-3 py-2">
            <Search className="h-4 w-4 shrink-0 text-txfaint" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Order number, customer or phone"
              aria-label="Search"
              className="w-full bg-transparent text-sm text-tx outline-none placeholder:text-txfaint"
            />
          </label>
        </div>
      </div>

      {state.loading && <LoadingBlock label="Reading orders…" />}
      {state.error && <ErrorBanner title="Could not read orders" message={state.error} />}

      {state.data && orders.length === 0 && (
        <Card>
          <EmptyState
            title="No orders"
            description="No orders match this filter. Orders appear here as soon as a customer completes one through the bot."
          />
        </Card>
      )}

      {orders.length > 0 && (
        <Card className="px-4 py-2 sm:px-5">
          <div className="divide-y divide-linesoft">
            {orders.map((order) => (
              <div key={order.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-tx">
                    {order.order_number || `Order #${order.id}`}
                    <span className="ml-2 text-xs font-normal text-txmuted">
                      {order.items?.length ?? 0} item{(order.items?.length ?? 0) === 1 ? '' : 's'}
                    </span>
                  </p>
                  <p className="mt-0.5 truncate text-xs text-txmuted">
                    {order.customer_name || order.wa_id || 'Unknown customer'}
                    {order.customer_mobile ? ` · ${order.customer_mobile}` : ''}
                    {order.order_type ? ` · ${order.order_type}` : ''}
                    {order.created_at ? ` · ${formatDate(order.created_at)}` : ''}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {order.status && (
                    <Pill tone={STATUS_TONE[order.status] ?? 'neutral'}>{order.status.replace('_', ' ')}</Pill>
                  )}
                  {order.payment_status && (
                    <Pill tone={PAYMENT_TONE[order.payment_status] ?? 'neutral'}>{order.payment_status}</Pill>
                  )}
                  <Pill tone="ok">{money(order.total_amount)}</Pill>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
