import { useState } from 'react'
import { HandCoins, Loader2, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import {
  DISTRIBUTOR_TIERS,
  type Distributor,
  type DistributorInput,
  money,
  operationsApi,
} from '@/lib/operations'
import { EMAIL_RE, isValidPhone } from '@/lib/profile'
import { cn } from '@/lib/cn'
import {
  Card,
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  FieldTextarea,
  GhostButton,
  NeedRow,
  Panel,
  Pill,
  type PillTone,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

const TIER_TONE: Record<string, PillTone> = {
  Bronze: 'neutral',
  Silver: 'neutral',
  Gold: 'warn',
  Platinum: 'ok',
}

interface DistributorFormShape {
  wa_id: string
  name: string
  phone: string
  email: string
  region: string
  city: string
  address: string
  service_area: string
  tier: string
  product_interests: string
  sales_volume: string
  outstanding_payments: string
  notes: string
}

function toShape(distributor?: Distributor | null): DistributorFormShape {
  return {
    wa_id: distributor?.wa_id ?? '',
    name: distributor?.name ?? '',
    phone: distributor?.phone ?? '',
    email: distributor?.email ?? '',
    region: distributor?.region ?? '',
    city: distributor?.city ?? '',
    address: distributor?.address ?? '',
    service_area: distributor?.service_area ?? '',
    tier: distributor?.tier ?? 'Bronze',
    product_interests: (distributor?.product_interests ?? []).join(', '),
    sales_volume: distributor ? String(distributor.sales_volume ?? '') : '',
    outstanding_payments: distributor ? String(distributor.outstanding_payments ?? '') : '',
    notes: distributor?.notes ?? '',
  }
}

/**
 * B2B distributor directory: tier, region, volumes and outstanding payments.
 * Reading needs `view_distributors`; adding, editing and deleting needs
 * `manage_distributors`.
 */
export function DistributorsPanel({ tenantId }: { tenantId: string }) {
  const { can } = useAuth()
  const toast = useToast()
  const action = useAction()
  const canManage = can('manage_distributors')

  const [tier, setTier] = useState('all')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<Distributor | null>(null)
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const state = useAsync(
    (signal) =>
      operationsApi.distributors(
        tenantId,
        { q: search || undefined, tier: tier === 'all' ? undefined : tier },
        signal,
      ),
    [tenantId, tier, search],
  )

  const distributors = state.data?.distributors ?? []
  const owing = distributors.filter((d) => d.outstanding_payments > 0)
  const owingTotal = owing.reduce((sum, d) => sum + d.outstanding_payments, 0)

  const submit = async (input: DistributorInput & { wa_id?: string }) => {
    if (editing) {
      const { wa_id, ...rest } = input
      const result = await action.run(() => operationsApi.updateDistributor(tenantId, editing.wa_id, rest))
      if (result) {
        toast.push('Distributor updated')
        setEditing(null)
        state.reload()
      }
      return
    }
    if (!input.wa_id?.trim()) return
    const { wa_id, ...rest } = input
    const result = await action.run(() =>
      operationsApi.createDistributor(tenantId, { ...rest, wa_id: wa_id.trim() }),
    )
    if (result) {
      toast.push('Distributor added')
      setCreating(false)
      state.reload()
    }
  }

  const remove = async (distributor: Distributor) => {
    const result = await action.run(() => operationsApi.deleteDistributor(tenantId, distributor.wa_id))
    if (result) {
      toast.push('Distributor deleted')
      setConfirmDelete(null)
      if (editing?.wa_id === distributor.wa_id) setEditing(null)
      state.reload()
    }
  }

  return (
    <div>
      <PageHeader
        title="Partners"
        description="Your distributors — authorized resellers with their tier, region, sales volume and outstanding payments."
        actions={
          canManage && !creating && !editing && (
            <PrimaryButton onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" />
              Add distributor
            </PrimaryButton>
          )
        }
        meta={state.data && <Pill>{distributors.length} shown</Pill>}
      />

      {owing.length > 0 && !creating && !editing && (
        <Panel title="Needs you" className="mb-4">
          <NeedRow icon={<HandCoins className="h-4 w-4" />}>
            <strong>{owing.length}</strong> of the partners below{' '}
            {owing.length === 1 ? 'has' : 'have'} outstanding payments totalling{' '}
            <strong>{money(owingTotal)}</strong>.
          </NeedRow>
        </Panel>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {['all', ...DISTRIBUTOR_TIERS].map((filter) => (
          <button
            key={filter}
            type="button"
            onClick={() => setTier(filter)}
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition',
              tier === filter
                ? 'bg-accsoft text-acc'
                : 'text-txmuted hover:bg-paper hover:text-tx',
            )}
          >
            {filter}
          </button>
        ))}
      </div>

      {!creating && !editing && (
        <div className="mb-4 max-w-sm">
          <label className="flex items-center gap-2 rounded-lg border border-line bg-inputbg px-3 py-2">
            <Search className="h-4 w-4 shrink-0 text-txfaint" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Name, phone, email or WhatsApp ID"
              aria-label="Search"
              className="w-full bg-transparent text-sm text-tx outline-none placeholder:text-txfaint"
            />
          </label>
        </div>
      )}

      {(creating || editing) && (
        <DistributorForm
          initial={editing}
          busy={action.busy}
          error={action.error}
          onCancel={() => {
            setCreating(false)
            setEditing(null)
          }}
          onSubmit={submit}
        />
      )}

      {state.loading && <LoadingBlock label="Reading distributors…" />}
      {state.error && <ErrorBanner title="Could not read distributors" message={state.error} />}

      {state.data && distributors.length === 0 && !creating && !editing && (
        <Card>
          <EmptyState
            title="No distributors"
            description="No distributors match this filter. Add one by hand, or let the bot's B2B flow register them."
            action={
              canManage ? (
                <GhostButton onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" />
                  Add distributor
                </GhostButton>
              ) : undefined
            }
          />
        </Card>
      )}

      {distributors.length > 0 && (
        <Card className="px-4 py-2 sm:px-5">
          <div className="divide-y divide-linesoft">
            {distributors.map((distributor) => (
              <div key={distributor.wa_id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-tx">
                    {distributor.name || distributor.wa_id}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-txmuted">
                    {distributor.wa_id}
                    {distributor.region ? ` · ${distributor.region}` : ''}
                    {distributor.phone && distributor.phone !== distributor.wa_id ? ` · ${distributor.phone}` : ''}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Pill tone={TIER_TONE[distributor.tier] ?? 'neutral'}>{distributor.tier}</Pill>
                  {distributor.sales_volume > 0 && (
                    <Pill tone="ok">{money(distributor.sales_volume)} volume</Pill>
                  )}
                  {distributor.outstanding_payments > 0 && (
                    <Pill tone="bad">{money(distributor.outstanding_payments)} outstanding</Pill>
                  )}
                  {canManage && (
                    <>
                      <GhostButton
                        className="px-2.5 py-1.5 text-xs"
                        onClick={() => setEditing(distributor)}
                      >
                        <Pencil className="h-3.5 w-3.5" /> Edit
                      </GhostButton>
                      {confirmDelete === distributor.wa_id ? (
                        <>
                          <DangerButton
                            className="px-2.5 py-1.5 text-xs"
                            disabled={action.busy}
                            onClick={() => remove(distributor)}
                          >
                            {action.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                            Delete forever
                          </DangerButton>
                          <GhostButton
                            className="px-2.5 py-1.5 text-xs"
                            onClick={() => setConfirmDelete(null)}
                          >
                            Cancel
                          </GhostButton>
                        </>
                      ) : (
                        <GhostButton
                          className="border-transparent bg-transparent px-2.5 py-1.5 text-xs text-badink hover:bg-badbg hover:text-badink"
                          onClick={() => setConfirmDelete(distributor.wa_id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" /> Delete
                        </GhostButton>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function DistributorForm({
  initial,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  initial: Distributor | null
  busy: boolean
  error: string | null
  onSubmit: (input: DistributorInput & { wa_id?: string }) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState<DistributorFormShape>(() => toShape(initial))
  const [touched, setTouched] = useState(false)

  const set = <K extends keyof DistributorFormShape>(key: K, value: DistributorFormShape[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const waIdMissing = touched && !initial && !form.wa_id.trim()
  const waIdInvalid =
    touched && !initial && form.wa_id.trim() !== '' && !/^\d{15}$/.test(form.wa_id.trim())
  const nameMissing = touched && !form.name.trim()
  const phoneMissing = touched && !form.phone.trim()
  const emailMissing = touched && !form.email.trim()
  const regionMissing = touched && !form.region.trim()

  const phoneValid = isValidPhone(form.phone.trim())
  const emailValid = !form.email.trim() || EMAIL_RE.test(form.email.trim())

  const submit = () => {
    setTouched(true)
    if (!initial && !form.wa_id.trim()) return
    if (waIdInvalid) return
    if (!form.name.trim()) return
    if (!form.phone.trim()) return
    if (emailMissing) return
    if (!phoneValid) return
    if (!emailValid) return
    if (!form.region.trim()) return
    onSubmit({
      wa_id: form.wa_id.trim(),
      name: form.name.trim(),
      phone: form.phone.trim(),
      email: form.email.trim(),
      region: form.region.trim(),
      city: form.city.trim(),
      address: form.address.trim(),
      service_area: form.service_area.trim(),
      tier: form.tier,
      product_interests: form.product_interests
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      sales_volume: Number(form.sales_volume) || 0,
      outstanding_payments: Number(form.outstanding_payments) || 0,
      notes: form.notes.trim(),
    })
  }

  return (
    <Panel
      title={initial ? `Edit ${initial.name || initial.wa_id}` : 'Add a distributor'}
      className="mb-4"
    >
      <p className="-mt-1 mb-4 text-sm text-txmuted">
        The B2B bot prices and prioritizes from the tier; product interests feed the campaign audience filters.
      </p>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <FieldInput
            label="WhatsApp ID"
            value={form.wa_id}
            error={
              waIdMissing
                ? 'A WhatsApp ID is required'
                : waIdInvalid
                  ? 'The WhatsApp phone number id must be exactly 15 digits.'
                  : undefined
            }
            onChange={(e) => set('wa_id', e.target.value)}
            placeholder="012345678901234"
            disabled={Boolean(initial)}
            hint={initial ? 'The ID a distributor is keyed by — it cannot change.' : undefined}
          />
          <FieldInput label="Name" value={form.name} error={nameMissing ? 'Name is required' : undefined} onChange={(e) => set('name', e.target.value)} />
          <FieldInput
            label="Phone"
            value={form.phone}
            error={
              phoneMissing
                ? 'Phone number is required'
                : !phoneValid
                  ? 'Phone must be a 10-digit number (or country code followed by 10 digits, e.g. +91 98765 43210)'
                  : undefined
            }
            onChange={(e) => set('phone', e.target.value)}
          />
          <FieldInput
            label="Email"
            value={form.email}
            error={
              emailMissing
                ? 'Email is required'
                : !emailValid
                  ? 'The email does not look valid.'
                  : undefined
            }
            onChange={(e) => set('email', e.target.value)}
          />
          <FieldInput label="Region" value={form.region} error={regionMissing ? 'Region is required' : undefined} onChange={(e) => set('region', e.target.value)} />
          <FieldInput label="City" value={form.city} onChange={(e) => set('city', e.target.value)} />
          <FieldSelect label="Tier" value={form.tier} onChange={(e) => set('tier', e.target.value)}>
            {DISTRIBUTOR_TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </FieldSelect>
        </div>

        <FieldInput
          label="Product interests"
          value={form.product_interests}
          onChange={(e) => set('product_interests', e.target.value)}
          hint="Comma separated. Campaign audience filters match against these."
          placeholder="chikki, snacks"
        />

        <FieldTextarea
          label="Address"
          rows={2}
          value={form.address}
          onChange={(e) => set('address', e.target.value)}
        />

        <FieldInput
          label="Service area"
          value={form.service_area}
          onChange={(e) => set('service_area', e.target.value)}
          hint="Localities this distributor covers."
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <FieldInput
            label="Sales volume"
            value={form.sales_volume}
            onChange={(e) => set('sales_volume', e.target.value)}
            prefix="₹"
            hint="Lifetime sales value — blank counts as 0."
          />
          <FieldInput
            label="Outstanding payments"
            value={form.outstanding_payments}
            onChange={(e) => set('outstanding_payments', e.target.value)}
            prefix="₹"
            hint="Unpaid balance the bot quotes when a distributor asks."
          />
        </div>

        <FieldTextarea label="Notes" rows={3} value={form.notes} onChange={(e) => set('notes', e.target.value)} />

        {error && <ErrorBanner title="Could not save" message={error} />}

        <div className="flex items-center gap-2">
          <PrimaryButton disabled={busy} onClick={submit}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {initial ? 'Save changes' : 'Add distributor'}
          </PrimaryButton>
          <GhostButton onClick={onCancel}>
            Cancel
          </GhostButton>
        </div>
      </div>
    </Panel>
  )
}
