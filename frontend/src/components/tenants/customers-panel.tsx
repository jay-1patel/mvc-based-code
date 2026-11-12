import { useState } from 'react'
import { useAsync } from '@/lib/hooks'
import { AlertTriangle, Download, Search } from 'lucide-react'
import { downloadCustomerExport, money, operationsApi } from '@/lib/operations'
import { relativeTime } from '@/lib/format'
import {
  Card,
  EmptyState,
  ErrorBanner,
  GhostButton,
  NeedRow,
  Panel,
  Pill,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

/**
 * Everyone the bot has sold to, taken a ticket from or chatted with,
 * aggregated by WhatsApp ID. Read-only; seeing it needs `view_customers`.
 */
export function CustomersPanel({ tenantId }: { tenantId: string }) {
  const toast = useToast()
  const [search, setSearch] = useState('')
  const [exporting, setExporting] = useState<'csv' | 'xlsx' | null>(null)

  const exportFile = async (format: 'csv' | 'xlsx') => {
    setExporting(format)
    try {
      await downloadCustomerExport(tenantId, format)
    } catch (err) {
      toast.push(err instanceof Error ? err.message : 'Export failed', 'error')
    } finally {
      setExporting(null)
    }
  }

  const state = useAsync(
    (signal) => operationsApi.tenantCustomers(tenantId, { q: search || undefined }, signal),
    [tenantId, search],
  )

  const customers = state.data?.customers ?? []
  const withOpenComplaints = customers.filter((c) => c.open_complaints > 0).length

  return (
    <div className="min-w-0">
      <PageHeader
        title="Customers"
        description="Customer details aggregated across orders, complaints and chat history, keyed by WhatsApp number."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <GhostButton onClick={() => exportFile('csv')} disabled={exporting !== null}>
              <Download className="h-4 w-4" />
              {exporting === 'csv' ? 'Exporting…' : 'CSV'}
            </GhostButton>
            <GhostButton onClick={() => exportFile('xlsx')} disabled={exporting !== null}>
              <Download className="h-4 w-4" />
              {exporting === 'xlsx' ? 'Exporting…' : 'Excel'}
            </GhostButton>
          </div>
        }
        meta={
          state.data && (
            <>
              <Pill>{state.data.count} shown</Pill>
              {withOpenComplaints > 0 && <Pill tone="warn">{withOpenComplaints} with open complaints</Pill>}
            </>
          )
        }
      />

      {withOpenComplaints > 0 && (
        <Panel title="Needs you" className="mb-4">
          <NeedRow icon={<AlertTriangle className="h-4 w-4" />}>
            <strong>{withOpenComplaints}</strong> {withOpenComplaints === 1 ? 'customer has' : 'customers have'}{' '}
            open complaints — they are marked in the list below.
          </NeedRow>
        </Panel>
      )}

      <div className="mb-4 max-w-sm">
        <label className="flex items-center gap-2 rounded-lg border border-line bg-inputbg px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-txfaint" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name, mobile or WhatsApp ID"
            aria-label="Search"
            className="w-full bg-transparent text-sm text-tx outline-none placeholder:text-txfaint"
          />
        </label>
      </div>

      {state.loading && <LoadingBlock label="Reading customers…" />}
      {state.error && <ErrorBanner title="Could not read customers" message={state.error} />}

      {state.data && customers.length === 0 && (
        <Card>
          <EmptyState
            title="No customers yet"
            description="Customers appear here once the bot has an order, complaint or conversation with them."
          />
        </Card>
      )}

      {customers.length > 0 && (
        <Card className="min-w-0 px-4 py-2 sm:px-5">
          <div className="divide-y divide-linesoft">
            {customers.map((customer) => (
              <div
                key={customer.wa_id}
                className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:gap-3"
              >
                {/* Full width on mobile (badges drop below); beside the badges from sm up. */}
                <div className="min-w-0 sm:flex-1">
                  <p className="break-words text-sm font-medium text-tx sm:truncate">
                    {customer.name || customer.wa_id}
                  </p>
                  <p className="mt-0.5 break-words text-xs text-txmuted sm:truncate">
                    {customer.wa_id}
                    {customer.mobile && customer.mobile !== customer.wa_id ? ` · ${customer.mobile}` : ''}
                    {customer.city ? ` · ${customer.city}` : ''}
                    {customer.company ? ` · ${customer.company}` : ''}
                    {customer.message_count ? ` · ${customer.message_count} messages` : ''}
                    {customer.last_active ? ` · last seen ${relativeTime(customer.last_active)}` : ''}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0 sm:justify-end">
                  <Pill tone="ok">{customer.total_orders} orders</Pill>
                  {customer.total_complaints > 0 && (
                    <Pill tone={customer.open_complaints > 0 ? 'bad' : 'neutral'}>
                      {customer.total_complaints} complaints
                    </Pill>
                  )}
                  <Pill tone="ok">{money(customer.total_spent)} spent</Pill>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
