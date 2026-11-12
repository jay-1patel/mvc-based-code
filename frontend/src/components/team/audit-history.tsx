import { useState, useMemo, useEffect, useCallback, type ReactNode } from 'react'
import { useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import { formatDate, relativeTime } from '@/lib/format'
import { auditApiExtended } from '@/lib/audit.extended'
import { cn } from '@/lib/cn'
import {
  Card,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  GhostButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  StatCard,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { ChevronLeft, ChevronRight, RefreshCw, Table2, Grid3X3, LineChart, Filter, Download, X } from 'lucide-react'
import type { AuditEventExtended, AuditFiltersExtended, AuditPageState, ViewMode, FilterPreset, AuditStatistics, AuditOutcome } from './audit-history.types'
import { LABELS, ACTION_CATEGORIES, getPresetDates, formatEventForDisplay, debounce, saveFiltersToStorage, loadFiltersFromStorage, getDefaultExportColumns } from './audit-history.utils'

const PAGE_SIZE = 50

/** Small toggle chip: accent-filled when active, outlined when not. */
function ChipButton({
  active,
  onClick,
  children,
  className,
}: {
  active?: boolean
  onClick: () => void
  children: ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition',
        active
          ? 'bg-acc font-semibold text-onacc hover:opacity-90'
          : 'border border-line bg-card font-medium text-tx hover:bg-paper',
        className,
      )}
    >
      {children}
    </button>
  )
}

// Filter Presets Component
const FilterPresets = ({ onApplyPreset }: { onApplyPreset: (preset: Record<string, unknown>) => void }) => {
  const [isOpen, setIsOpen] = useState(false)

  const presets = [
    { name: 'Today', filters: getPresetDates('today') },
    { name: 'Last 7 days', filters: getPresetDates('last_7_days') },
    { name: 'Last 30 days', filters: getPresetDates('last_30_days') },
    { name: 'Last 90 days', filters: getPresetDates('last_90_days') },
    { name: 'All time', filters: {} },
    { name: 'Successful only', filters: { outcome: 'success' } },
    { name: 'Failed only', filters: { outcome: 'failure' } },
  ]

  return (
    <div className="relative">
      <GhostButton className="px-3 py-1.5 text-xs" onClick={() => setIsOpen(!isOpen)}>
        <Filter className="h-4 w-4" />
        Presets
      </GhostButton>
      {isOpen && (
        <div className="absolute right-0 z-50 mt-2 w-48 rounded-lg border border-line bg-card shadow-lg">
          <div className="py-1">
            {presets.map((preset) => (
              <button
                key={preset.name}
                onClick={() => {
                  onApplyPreset({ ...preset.filters, preset: preset.name.toLowerCase().replace(/\s+/g, '_') })
                  setIsOpen(false)
                }}
                className="block w-full px-4 py-2 text-left text-sm text-tx hover:bg-paper"
              >
                {preset.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Export Menu Component
const ExportMenu = ({ events, filters, onExportSuccess, onExportError }: { events: AuditEventExtended[]; filters: AuditFiltersExtended; onExportSuccess: () => void; onExportError: (message: string) => void }) => {
  const [isOpen, setIsOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)

  const handleExport = async (format: 'csv' | 'json' | 'pdf') => {
    setIsExporting(true)
    try {
      const columns = getDefaultExportColumns()
      const request = { filters, format, columns }
      const response = await auditApiExtended.exportEvents(request)

      // Create download link
      const url = window.URL.createObjectURL(new Blob([response]))
      const link = document.createElement('a')
      link.href = url
      link.download = `audit-history-${new Date().toISOString().split('T')[0]}.${format}`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)

      onExportSuccess()
    } catch (error) {
      // Export failures must be visible on the page, not just the console.
      onExportError(error instanceof Error ? error.message : 'The export failed. Please try again.')
    } finally {
      setIsExporting(false)
      setIsOpen(false)
    }
  }

  return (
    <div className="relative">
      <GhostButton className="px-3 py-1.5 text-xs" onClick={() => setIsOpen(!isOpen)} disabled={isExporting || events.length === 0}>
        <Download className="h-4 w-4" />
        Export
      </GhostButton>
      {isOpen && (
        <div className="absolute right-0 z-50 mt-2 w-40 rounded-lg border border-line bg-card p-2 shadow-lg">
          <button
            onClick={() => handleExport('csv')}
            className="block w-full rounded-md px-3 py-2 text-left text-sm text-tx hover:bg-paper"
          >
            Export as CSV
          </button>
          <button
            onClick={() => handleExport('pdf')}
            className="block w-full rounded-md px-3 py-2 text-left text-sm text-tx hover:bg-paper"
          >
            Export as PDF
          </button>
          <button
            onClick={() => handleExport('json')}
            className="block w-full rounded-md px-3 py-2 text-left text-sm text-tx hover:bg-paper"
          >
            Export as JSON
          </button>
        </div>
      )}
    </div>
  )
}

// Date Range Picker Component
const DateRangePicker = ({ startDate, endDate, preset, onChange }: { startDate: string; endDate: string; preset: FilterPreset; onChange: (preset: FilterPreset, startDate: string, endDate: string) => void }) => {
  const handlePresetChange = (newPreset: FilterPreset) => {
    const dates = getPresetDates(newPreset)
    onChange(newPreset, dates.start_date, dates.end_date)
  }

  return (
    <div className="flex flex-wrap gap-1">
      {(['today', 'last_7_days', 'last_30_days', 'last_90_days'] as FilterPreset[]).map((p) => (
        <ChipButton
          key={p}
          active={preset === p}
          onClick={() => handlePresetChange(p)}
        >
          {p.replace(/_/g, ' ')}
        </ChipButton>
      ))}
      {preset === 'custom' && (
        <div className="flex gap-2">
          <FieldInput type="date" value={startDate} onChange={(e) => onChange('custom', e.target.value, endDate)} />
          <FieldInput type="date" value={endDate} onChange={(e) => onChange('custom', startDate, e.target.value)} />
        </div>
      )}
    </div>
  )
}

// Audit Event Details Component
const AuditEventDetails = ({ event }: { event: AuditEventExtended }) => {
  const details = event.details || {}

  // Format detail values for display
  const formatDetailValue = (value: unknown): string => {
    if (value === null || value === undefined || value === '') return '—'
    if (typeof value === 'boolean') return value ? 'Yes' : 'No'
    if (Array.isArray(value)) return value.map(formatDetailValue).join(', ')
    if (typeof value === 'object') {
      return Object.entries(value as Record<string, unknown>)
        .map(([key, val]) => `${key.replace(/_/g, ' ')}: ${formatDetailValue(val)}`)
        .join(' | ')
    }
    return String(value)
  }

  // Check for specific detail fields to highlight
  const getHighlightedDetails = () => {
    const parts: string[] = []

    if (details.name || details.title) {
      parts.push(`Name: ${details.name || details.title}`)
    }
    if (details.description) {
      parts.push(`Description: ${details.description}`)
    }
    if (details.role) {
      parts.push(`Role: ${details.role}`)
    }
    if (details.role_before && details.role_after) {
      parts.push(`Role changed: ${details.role_before} → ${details.role_after}`)
    }
    if (details.version) {
      parts.push(`Version: ${details.version}`)
    }
    if (details.decision) {
      parts.push(`Decision: ${details.decision}`)
    }
    if (details.api_type) {
      parts.push(`API Type: ${details.api_type}`)
    }
    if (details.scope) {
      parts.push(`Scope: ${details.scope}`)
    }
    if (Array.isArray(details.changed_fields) && details.changed_fields.length > 0) {
      parts.push(`Fields: ${details.changed_fields.join(', ')}`)
    }
    if (Array.isArray(details.changed_sections) && details.changed_sections.length > 0) {
      parts.push(`Sections: ${details.changed_sections.join(', ')}`)
    }
    if (Array.isArray(details.permission_keys_changed) && details.permission_keys_changed.length > 0) {
      parts.push(`Permissions: ${details.permission_keys_changed.join(', ')}`)
    }
    if (typeof details.configured === 'boolean') {
      parts.push(details.configured ? 'Configured' : 'Not configured')
    }
    if (details.label) {
      parts.push(`Label: ${details.label}`)
    }

    // Fallback: show all details if no specific ones matched
    if (parts.length === 0 && Object.keys(details).length > 0) {
      return Object.entries(details)
        .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${formatDetailValue(value)}`)
        .join(' | ')
    }

    return parts.join(' | ') || 'No additional details'
  }

  return (
    <div className="text-sm text-txmuted">
      {getHighlightedDetails()}
    </div>
  )
}

// Table View Component
const TableView = ({ events, sortBy, sortOrder, onSort }: { events: AuditEventExtended[]; sortBy: string; sortOrder: 'asc' | 'desc'; onSort: (column: string) => void }) => {
  const getSortIcon = (column: string) => {
    if (sortBy !== column) return null
    return sortOrder === 'asc' ? '↑' : '↓'
  }

  const thClass = 'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-txmuted'
  const sortableTh = `${thClass} cursor-pointer hover:bg-paper`

  return (
    <div className="overflow-x-auto rounded-panel border border-line bg-card">
      <table className="w-full">
        <thead className="bg-paper">
          <tr>
            <th onClick={() => onSort('created_at')} className={sortableTh}>
              Date/Time {getSortIcon('created_at')}
            </th>
            <th onClick={() => onSort('action')} className={sortableTh}>
              Action {getSortIcon('action')}
            </th>
            <th onClick={() => onSort('actor_username')} className={sortableTh}>
              Actor {getSortIcon('actor_username')}
            </th>
            <th className={thClass}>
              Target
            </th>
            <th onClick={() => onSort('tenant_label')} className={sortableTh}>
              Tenant {getSortIcon('tenant_label')}
            </th>
            <th onClick={() => onSort('outcome')} className={sortableTh}>
              Outcome {getSortIcon('outcome')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-linesoft bg-card">
          {events.map((event) => {
            const formattedEvent = formatEventForDisplay(event)
            return (
              <tr key={event.id} className="hover:bg-paper">
                <td className="px-4 py-3 whitespace-nowrap text-sm text-tx">
                  {formattedEvent.formatted_date}
                  <div className="text-xs text-txfaint">{formattedEvent.relative_time}</div>
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-sm text-tx">
                  {LABELS[event.action] || event.action.replace(/_/g, ' ')}
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-sm text-tx">
                  {event.actor_label || event.actor_username || 'System'}
                  {event.actor_role && <div className="text-xs text-txfaint">{event.actor_role}</div>}
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-sm text-tx">
                  {event.target_username || '—'}
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-sm text-tx">
                  {formattedEvent.tenant_label}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <Pill tone={event.outcome === 'success' ? 'ok' : 'bad'}>
                    {event.outcome}
                  </Pill>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Statistics View Component
const StatisticsView = ({ statistics, events }: { statistics: AuditStatistics; events: AuditEventExtended[] }) => {
  return (
    <Panel title="Audit Statistics" className="mb-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard value={statistics.total_events.toLocaleString()} label="Total Events" />
        <StatCard value={statistics.by_outcome.success.toLocaleString()} label="Success" />
        <StatCard
          value={statistics.by_outcome.failure.toLocaleString()}
          label="Failures"
          tone={statistics.by_outcome.failure ? 'failure' : 'normal'}
        />
        <StatCard value={Object.keys(statistics.by_actor || {}).length} label="Unique Actors" />
      </div>
      {events.length > 0 && (
        <div className="mt-4 border-t border-linesoft pt-4">
          <h4 className="mb-2 text-sm font-semibold text-tx">Top Actions</h4>
          <div className="flex flex-wrap gap-2">
            {Object.entries(statistics.by_action || {})
              .sort(([, a], [, b]) => (b as number) - (a as number))
              .slice(0, 5)
              .map(([action, count]) => (
                <Pill key={action}>
                  {LABELS[action as keyof typeof LABELS] || action.replace(/_/g, ' ')}: {count}
                </Pill>
              ))}
          </div>
        </div>
      )}
    </Panel>
  )
}

// Timeline View Component
const TimelineView = ({ events }: { events: AuditEventExtended[] }) => {
  const eventsByDate = useMemo(() => {
    const grouped: Record<string, AuditEventExtended[]> = {}
    events.forEach(event => {
      const date = new Date(event.created_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
      ;(grouped[date] ??= []).push(event)
    })
    return grouped
  }, [events])

  return (
    <div className="space-y-8">
      {Object.entries(eventsByDate).map(([date, dateEvents]) => (
        <section key={date} className="space-y-3">
          <h3 className="text-lg font-semibold text-tx">
            {date} <span className="text-xs font-normal text-txfaint">{dateEvents.length} events</span>
          </h3>
          <div className="space-y-3 border-l border-line pl-4">
            {dateEvents.map(event => {
              const formattedEvent = formatEventForDisplay(event)
              return (
                <Card key={event.id} className="p-4 sm:p-5">
                  <div className="mb-2 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h4 className="text-base font-semibold text-tx">
                        {LABELS[event.action] || event.action.replace(/_/g, ' ')}
                      </h4>
                      <p className="mt-0.5 text-sm text-txmuted">
                        {`${event.actor_label || event.actor_username || 'System'}${event.actor_role ? ` · ${event.actor_role}` : ''} · ${relativeTime(event.created_at)}`}
                      </p>
                    </div>
                    <Pill tone={event.outcome === 'success' ? 'ok' : 'bad'}>{event.outcome}</Pill>
                  </div>
                  <div className="space-y-3">
                    <div className="flex flex-wrap gap-3 text-xs text-txmuted">
                      <span>Tenant: {formattedEvent.tenant_label || event.tenant_name || event.tenant_id || 'Platform'}{event.tenant_slug ? ` · ${event.tenant_slug}` : ''}</span>
                      <span>IP: {event.ip_address || '—'}</span>
                      <span>Resource: {event.resource_type || '—'}{event.resource_id ? `/${event.resource_id}` : ''}</span>
                    </div>
                    <AuditEventDetails event={event} />
                  </div>
                </Card>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

// Main AuditHistory Component
export function AuditHistory() {
  const { identity } = useAuth()
  // Tenant admins get this page scoped to their own tenant (role-panels
  // item 12): the filter is forced here and enforced again by the API.
  const scopedTenant = identity?.role === 'admin' ? identity.tenant_id ?? '' : ''
  const [exportError, setExportError] = useState<string | null>(null)
  const [filters, setFilters] = useState<AuditFiltersExtended>({
    limit: PAGE_SIZE,
    offset: 0,
    sort_by: 'created_at',
    sort_order: 'desc'
  })
  const [draft, setDraft] = useState({
    search: '',
    actor: '',
    action: '',
    category: '',
    tenant_id: '',
    outcome: '' as AuditOutcome | '',
    start_date: '',
    end_date: '',
    preset: 'last_7_days' as FilterPreset,
  })
  const [viewMode, setViewMode] = useState<ViewMode>('table')
  const [showRefreshing, setShowRefreshing] = useState(false)
  const [toastMessage, setToastMessage] = useState<string | null>(null)
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)
  const [showStats, setShowStats] = useState(false)

  // Load saved filters from localStorage
  useEffect(() => {
    const savedFilters = loadFiltersFromStorage()
    if (savedFilters) {
      setDraft(prev => ({ ...prev, ...savedFilters }))
      // Apply saved date preset
      if (savedFilters.preset) {
        const preset = savedFilters.preset as FilterPreset
        const dates = getPresetDates(preset)
        setDraft(prev => ({ ...prev, ...dates, preset }))
      }
    }
  }, [])

  const requestFilters = useMemo(() => ({
    ...filters,
    limit: filters.limit ?? PAGE_SIZE,
    start_date: draft.start_date || undefined,
    end_date: draft.end_date || undefined,
    actor: draft.actor || filters.actor,
    action: draft.action || filters.action,
    category: draft.category || filters.category,
    tenant_id: scopedTenant || draft.tenant_id || filters.tenant_id,
    outcome: draft.outcome || filters.outcome,
    search: draft.search || filters.search,
  }), [filters, draft, scopedTenant])

  // Fetch audit data
  const state = useAsync(() => auditApiExtended.list(requestFilters) as Promise<AuditPageState>, [JSON.stringify(requestFilters)])
  const page = state.data
  const events = page?.events ?? []
  const formattedEvents = useMemo(() => events.map(formatEventForDisplay), [events])

  // Fetch statistics
  const statistics = useAsync(() =>
    auditApiExtended.getStatistics(filters) as Promise<AuditStatistics>,
    [JSON.stringify(filters)]
  )

  const start = page && page.total ? page.offset + 1 : 0
  const end = page ? page.offset + page.events.length : 0

  // Apply filters
  const applyFilters = useCallback((nextDraft = draft) => {
    const next: AuditFiltersExtended = {
      start_date: nextDraft.start_date || undefined,
      end_date: nextDraft.end_date || undefined,
      actor: nextDraft.actor || undefined,
      action: nextDraft.action || undefined,
      category: nextDraft.category || undefined,
      tenant_id: nextDraft.tenant_id || undefined,
      outcome: nextDraft.outcome || undefined,
      search: nextDraft.search || undefined,
      limit: PAGE_SIZE,
      offset: 0,
      sort_by: filters.sort_by,
      sort_order: filters.sort_order,
    }
    setFilters(next)
    saveFiltersToStorage(nextDraft)
  }, [draft, filters.sort_by, filters.sort_order])

  // Debounced search
  const debouncedSearch = useMemo(() => debounce((value: string) => {
    setDraft(prev => ({ ...prev, search: value }))
    applyFilters({ ...draft, search: value })
  }, 300), [applyFilters, draft])

  const handleSort = (column: string) => {
    setFilters(current => ({
      ...current,
      sort_by: column || 'created_at',
      sort_order: current.sort_by === column && current.sort_order === 'desc' ? 'asc' : 'desc',
      offset: 0,
    }))
  }

  const updateDraft = <K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) => {
    setDraft(current => ({ ...current, [key]: value }))
  }

  const clearFilters = () => {
    const clean = {
      search: '',
      actor: '',
      action: '',
      category: '',
      tenant_id: '',
      outcome: '' as AuditOutcome | '',
      start_date: '',
      end_date: '',
      preset: 'last_7_days' as FilterPreset
    }
    setDraft(clean)
    setFilters({ limit: PAGE_SIZE, offset: 0, sort_by: 'created_at', sort_order: 'desc' })
    saveFiltersToStorage(clean)
  }

  const refresh = () => {
    setShowRefreshing(true)
    state.reload()
    setTimeout(() => setShowRefreshing(false), 600)
  }

  const notifyExport = () => {
    setExportError(null)
    setToastMessage('Audit export downloaded')
    setTimeout(() => setToastMessage(null), 2500)
  }

  const applyPreset = (preset: Record<string, unknown>) => {
    const next = { ...draft, ...preset } as typeof draft
    setDraft(next)
    applyFilters(next)
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Activity log"
        description={scopedTenant
          ? 'Activity in your tenant only, with actor, client, request and change details.'
          : 'Review administrator activity across tenants, with actor, client, request and change details.'}
        actions={
          <div className="flex flex-wrap gap-2">
            <FilterPresets onApplyPreset={applyPreset} />
            <ExportMenu events={formattedEvents} filters={requestFilters} onExportSuccess={notifyExport} onExportError={setExportError} />
            <GhostButton
              className="px-3 py-1.5 text-xs"
              onClick={refresh}
              disabled={state.loading}
            >
              <RefreshCw className={`h-4 w-4 ${showRefreshing ? 'animate-spin' : ''}`} />
              Refresh
            </GhostButton>
          </div>
        }
      />

      {toastMessage && <Notice tone="success">{toastMessage}</Notice>}
      {exportError && <ErrorBanner title="Export failed" message={exportError} />}
      {state.error && <ErrorBanner title="Could not load audit history" message={String(state.error)} />}
      {state.loading && <LoadingBlock label="Loading audit history..." />}

      {/* Filter Card */}
      <Panel
        title="Filters"
        action={
          <GhostButton className="px-3 py-1.5 text-xs" onClick={() => setShowAdvancedFilters(v => !v)}>
            {showAdvancedFilters ? 'Hide' : 'More'} filters
          </GhostButton>
        }
      >
        <p className="-mt-1 mb-4 text-sm text-txmuted">
          Search actors, clients, IP addresses, user agents and safe event details.
        </p>
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <FieldInput
              label="Search"
              placeholder="Filename, IP, actor, change..."
              value={draft.search}
              onChange={e => {
                updateDraft('search', e.target.value);
                debouncedSearch(e.target.value)
              }}
            />
            <FieldInput
              label="Actor"
              placeholder="Username"
              value={draft.actor}
              onChange={e => updateDraft('actor', e.target.value)}
            />
            <FieldSelect
              label="Action"
              value={draft.action}
              onChange={e => updateDraft('action', e.target.value)}
            >
              <option value="">All actions</option>
              {Object.entries(LABELS).map(([action, label]) => (
                <option key={action} value={action}>{label}</option>
              ))}
            </FieldSelect>
            <FieldSelect
              label="Outcome"
              value={draft.outcome}
              onChange={e => updateDraft('outcome', e.target.value as AuditOutcome | '')}
            >
              <option value="">All outcomes</option>
              <option value="success">Success</option>
              <option value="failure">Failure</option>
            </FieldSelect>
          </div>

          {showAdvancedFilters && (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <FieldSelect
                label="Category"
                value={draft.category}
                onChange={e => updateDraft('category', e.target.value)}
              >
                <option value="">All categories</option>
                {Object.entries(ACTION_CATEGORIES).map(([key, value]) => (
                  <option key={key} value={key}>{value.label}</option>
                ))}
              </FieldSelect>
              <FieldInput
                label="Tenant / client ID"
                placeholder="Tenant ID"
                value={scopedTenant || draft.tenant_id}
                disabled={Boolean(scopedTenant)}
                onChange={e => updateDraft('tenant_id', e.target.value)}
              />
              <div className="flex items-end gap-2">
                <DateRangePicker
                  startDate={draft.start_date}
                  endDate={draft.end_date}
                  preset={draft.preset}
                  onChange={(preset, start_date, end_date) => {
                    setDraft(current => ({ ...current, preset, start_date, end_date }))
                  }}
                />
              </div>
              <div className="flex items-end gap-2">
                <PrimaryButton onClick={() => applyFilters()}>
                  Apply filters
                </PrimaryButton>
                <GhostButton onClick={clearFilters}>
                  <X className="h-4 w-4" /> Clear
                </GhostButton>
              </div>
            </div>
          )}

          {!showAdvancedFilters && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-txmuted">Date range</span>
              {(['today', 'last_7_days', 'last_30_days', 'last_90_days'] as FilterPreset[]).map(preset => (
                <ChipButton
                  key={preset}
                  active={draft.preset === preset}
                  onClick={() => {
                    const dates = getPresetDates(preset)
                    const next = { ...draft, preset, ...dates }
                    setDraft(next)
                    applyFilters(next)
                  }}
                >
                  {preset.replace(/_/g, ' ')}
                </ChipButton>
              ))}
            </div>
          )}
        </div>
      </Panel>

      {/* View Mode Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ChipButton
            active={viewMode === 'table'}
            onClick={() => setViewMode('table')}
          >
            <Table2 className="h-4 w-4" />
            Table
          </ChipButton>
          <ChipButton
            active={viewMode === 'cards'}
            onClick={() => setViewMode('cards')}
          >
            <Grid3X3 className="h-4 w-4" />
            Cards
          </ChipButton>
          <ChipButton
            active={viewMode === 'timeline'}
            onClick={() => setViewMode('timeline')}
          >
            <LineChart className="h-4 w-4" />
            Timeline
          </ChipButton>
        </div>
        <GhostButton className="px-3 py-1.5 text-xs" onClick={() => setShowStats(v => !v)}>
          {showStats ? 'Hide' : 'Show'} summary
        </GhostButton>
      </div>

      {/* Statistics Summary */}
      {showStats && statistics.data && (
        <StatisticsView statistics={statistics.data} events={formattedEvents} />
      )}

      {/* Empty State */}
      {page?.events.length === 0 && !state.loading && (
        <Card>
          <EmptyState title="No activity found" description="Try changing the filters or date range." />
        </Card>
      )}

      {/* Results */}
      {page && page.events.length > 0 && (
        <>
          <div className="flex items-center justify-between text-xs text-txmuted">
            <span>Showing {start}–{end} of {page.total.toLocaleString()} events</span>
            <span>Sort: {filters.sort_by || 'Date'} ({filters.sort_order})</span>
          </div>

          {viewMode === 'table' && (
            <TableView
              events={formattedEvents}
              sortBy={filters.sort_by || ''}
              sortOrder={filters.sort_order || 'desc'}
              onSort={handleSort}
            />
          )}

          {viewMode === 'cards' && (
            <div className="space-y-3">
              {formattedEvents.map(event => {
                return (
                  <Card key={event.id} className="p-4 sm:p-5">
                    <div className="mb-2 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="text-base font-semibold text-tx">
                          {LABELS[event.action] || event.action.replace(/_/g, ' ')}
                        </h4>
                        <p className="mt-0.5 text-sm text-txmuted">
                          {`${event.actor_label || event.actor_username || 'System'}${event.actor_role ? ` · ${event.actor_role}` : ''} · ${formatDate(event.created_at)}`}
                        </p>
                      </div>
                      <Pill tone={event.outcome === 'success' ? 'ok' : 'bad'}>{event.outcome}</Pill>
                    </div>
                    <AuditEventDetails event={event} />
                  </Card>
                )
              })}
            </div>
          )}

          {viewMode === 'timeline' && <TimelineView events={formattedEvents} />}

          {/* Pagination */}
          <div className="mt-4 flex items-center justify-between">
            <GhostButton
              disabled={page.offset === 0 || state.loading}
              onClick={() => setFilters(v => ({ ...v, offset: Math.max(0, (v.offset ?? 0) - PAGE_SIZE) }))}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </GhostButton>
            <span className="text-xs text-txmuted">
              Page {Math.floor(page.offset / page.limit) + 1} of {Math.max(1, Math.ceil(page.total / page.limit))}
            </span>
            <GhostButton
              disabled={page.offset + page.events.length >= page.total || state.loading}
              onClick={() => setFilters(v => ({ ...v, offset: (v.offset ?? 0) + PAGE_SIZE }))}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </GhostButton>
          </div>
        </>
      )}
    </div>
  )
}

export type { ViewMode, FilterPreset } from './audit-history.types'

export default AuditHistory
