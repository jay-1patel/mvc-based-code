import { useMemo, useState } from 'react'
import { Loader2, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import { contentWritePerms } from '@/lib/permissions'
import { offeringsApi, useOfferings } from '@/lib/offerings'
import { useTenants } from '@/lib/tenants'
import { getVertical } from '@/lib/verticals'
import type { Offering, OfferingInput } from '@/lib/types'
import { cn } from '@/lib/cn'
import {
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldTextarea,
  GhostButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  Toggle,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

/** The compact size the legacy `size="sm"` buttons had on this screen. */
const smallBtn = 'px-3 py-1.5 text-xs'

/**
 * The tenant's content panel: what the bot can list, describe and quote.
 *
 * One record shape for every vertical, so this screen only changes its
 * vocabulary — "Services" and a tech stack for a software tenant, "Packages" and
 * an itinerary for a travel tenant. Rows are tenant-owned on the server: the
 * tenant is a path segment and a token for another tenant is refused.
 */
export function OfferingsPanel({ tenantId }: { tenantId: string }) {
  const { active } = useTenants()
  const { can } = useAuth()
  const toast = useToast()
  const action = useAction()

  const content = getVertical(active?.vertical).content
  const canRead = can('view_products')
  // Add/edit/remove are granted separately per vertical — see contentWritePerms.
  const { add: addPerms, edit: editPerms, remove: removePerms } = contentWritePerms(active?.vertical)
  const canAdd = addPerms.some(can)
  const canEditRow = editPerms.some(can)
  const canRemove = removePerms.some(can)
  const canEdit = canAdd || canEditRow || canRemove

  const [showInactive, setShowInactive] = useState(false)
  const { offerings, loading, error, reload } = useOfferings(tenantId, showInactive)
  const [editing, setEditing] = useState<Offering | null>(null)
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  const groups = useMemo(() => {
    const map = new Map<string, Offering[]>()
    for (const item of offerings) {
      const key = item.category || 'Uncategorised'
      const bucket = map.get(key)
      if (bucket) bucket.push(item)
      else map.set(key, [item])
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [offerings])

  const remove = async (id: number, hard: boolean) => {
    const result = await action.run(() => offeringsApi.remove(tenantId, id, hard))
    if (result) {
      setConfirmDelete(null)
      reload()
      toast.push(hard ? 'Deleted permanently' : 'Removed — the bot will not list it again')
    }
  }

  if (!canRead) {
    return (
      <div>
        <PageHeader title={content.label} description="What this tenant sells, offers or lists." />
        <Notice tone="warn" title="Not permitted">
          Viewing {content.label.toLowerCase()} needs the <strong>view_products</strong> permission.
        </Notice>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title={content.label}
        description={`Add, edit and delete what ${active?.display_name || 'this tenant'} offers. The bot answers from these — a ${content.singular} listed here is one the bot can describe, price and quote.`}
        actions={
          canAdd ? (
            <PrimaryButton
              disabled={creating || editing !== null}
              onClick={() => setCreating(true)}
            >
              <Plus className="h-4 w-4" />
              Add a {content.singular}
            </PrimaryButton>
          ) : undefined
        }
      />

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}
      {!canEdit && (
        <Notice tone="warn" title="Read-only" className="mb-4">
          Adding, editing and deleting needs the catalogue write permissions for
          this tenant's vertical.
        </Notice>
      )}

      {creating && (
        <OfferingForm
          content={content}
          busy={action.busy}
          onCancel={() => setCreating(false)}
          onSubmit={async (body) => {
            const result = await action.run(() => offeringsApi.create(tenantId, body))
            if (result) {
              setCreating(false)
              reload()
              toast.push(`${result.name} added`)
            }
          }}
        />
      )}

      {editing && (
        <OfferingForm
          content={content}
          initial={editing}
          busy={action.busy}
          onCancel={() => setEditing(null)}
          onSubmit={async (body) => {
            const result = await action.run(() => offeringsApi.update(tenantId, editing.id, body))
            if (result) {
              setEditing(null)
              reload()
              toast.push('Saved')
            }
          }}
        />
      )}

      <Panel
        title={`${offerings.length} ${offerings.length === 1 ? content.singular : content.label.toLowerCase()}`}
        className="mt-4"
        action={
          <div className="w-56">
            <Toggle
              size="sm"
              checked={showInactive}
              onChange={setShowInactive}
              label="Show removed"
            />
          </div>
        }
      >
        {loading ? (
          <LoadingBlock label={`Loading ${content.label.toLowerCase()}…`} />
        ) : error ? (
          <ErrorBanner title="Could not load" message={error} />
        ) : offerings.length === 0 ? (
          <EmptyState
            title={`No ${content.label.toLowerCase()} yet`}
            description={
              canEdit
                ? `Add the first ${content.singular} and the bot can start answering questions about it.`
                : 'Nothing has been added for this tenant yet.'
            }
          />
        ) : (
          <div className="space-y-5">
            {groups.map(([category, items]) => (
              <div key={category}>
                <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-txmuted">
                  {category}
                </p>
                <div className="divide-y divide-linesoft rounded-panel border border-line">
                  {items.map((item) => (
                    <div key={item.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-tx">{item.name}</span>
                          {!item.is_active && <Pill>removed</Pill>}
                          {item.short_label && <Pill tone="ok">{item.short_label}</Pill>}
                          {item.price && <span className="text-xs text-txfaint">{item.price}</span>}
                        </div>
                        {item.short_description && (
                          <p className="mt-1 text-xs leading-relaxed text-txmuted">
                            {item.short_description}
                          </p>
                        )}
                        {Object.entries(item.attrs || {}).filter(([, v]) => v).length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {Object.entries(item.attrs)
                              .filter(([, v]) => v)
                              .map(([k, v]) => (
                                <span
                                  key={k}
                                  className="rounded bg-paper px-1.5 py-0.5 text-2xs text-txmuted ring-1 ring-inset ring-line"
                                >
                                  {k.replace(/_/g, ' ')}: {v}
                                </span>
                              ))}
                          </div>
                        )}
                      </div>
                      {(canEditRow || canRemove) && (
                        <div className="flex shrink-0 items-center gap-2">
                          {canEditRow && (
                            <GhostButton
                              className={smallBtn}
                              onClick={() => setEditing(item)}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                              Edit
                            </GhostButton>
                          )}
                          {canRemove && item.is_active &&
                            (confirmDelete === item.id ? (
                              <>
                                <DangerButton
                                  className={smallBtn}
                                  disabled={action.busy}
                                  onClick={() => remove(item.id, true)}
                                >
                                  {action.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                                  Delete forever
                                </DangerButton>
                                <GhostButton
                                  className={smallBtn}
                                  onClick={() => setConfirmDelete(null)}
                                >
                                  Cancel
                                </GhostButton>
                              </>
                            ) : (
                              <GhostButton
                                className={smallBtn}
                                onClick={() => setConfirmDelete(item.id)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                                Remove
                              </GhostButton>
                            ))}
                          {canEditRow && !item.is_active && (
                            <GhostButton
                              className={smallBtn}
                              disabled={action.busy}
                              onClick={() => action.run(() => offeringsApi.update(tenantId, item.id, { is_active: true })).then((r) => r && reload())}
                            >
                              {action.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                              Restore
                            </GhostButton>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

type FormShape = {
  name: string
  category: string
  short_label: string
  short_description: string
  description: string
  price: string
  detail_url: string
  attrs: Record<string, string>
  is_active: boolean
}

function toShape(item?: Offering, defaults: string[] = []): FormShape {
  return {
    name: item?.name ?? '',
    category: item?.category || defaults[0] || 'general',
    short_label: item?.short_label ?? '',
    short_description: item?.short_description ?? '',
    description: item?.description ?? '',
    price: item?.price ?? '',
    detail_url: item?.detail_url ?? '',
    attrs: { ...(item?.attrs ?? {}) },
    is_active: item?.is_active ?? true,
  }
}

function OfferingForm({
  content,
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  content: ReturnType<typeof getVertical>['content']
  initial?: Offering
  busy: boolean
  onSubmit: (body: OfferingInput) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState<FormShape>(() => toShape(initial, content.categories))
  const [touched, setTouched] = useState(false)

  const set = <K extends keyof FormShape>(key: K, value: FormShape[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const nameMissing = touched && !form.name.trim()

  const submit = () => {
    setTouched(true)
    if (!form.name.trim()) return
    const attrs: Record<string, string> = {}
    for (const [k, v] of Object.entries(form.attrs)) if (v.trim()) attrs[k] = v.trim()
    onSubmit({
      name: form.name.trim(),
      category: form.category.trim() || 'general',
      short_label: form.short_label.trim(),
      short_description: form.short_description.trim(),
      description: form.description.trim(),
      price: form.price.trim() || null,
      detail_url: form.detail_url.trim(),
      media_url: '',
      media_type: 'image',
      attrs,
      sort_order: initial?.sort_order ?? 0,
      is_active: form.is_active,
    })
  }

  return (
    <Panel
      title={initial ? `Edit ${initial.name}` : `Add a ${content.singular}`}
      className="mt-4"
      action={
        <GhostButton className={smallBtn} onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
          Close
        </GhostButton>
      }
    >
      {content.extraFields.length > 0 && (
        <p className="-mt-1 mb-4 text-sm text-txmuted">
          {`Everything except ${content.extraFields.map((f) => f.label.toLowerCase()).join(', ')} is shared with the bot's answers.`}
        </p>
      )}
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <FieldInput
            label={`${content.label} name`}
            value={form.name}
            error={nameMissing ? 'A name is required' : undefined}
            onChange={(e) => set('name', e.target.value)}
            placeholder={content.singular === 'service' ? 'Web development' : 'Name'}
          />
          <FieldInput
            label="Group"
            value={form.category}
            onChange={(e) => set('category', e.target.value)}
            hint="Free text. These become the groups in the list above."
            list="offering-groups"
          />
          <datalist id="offering-groups">
            {content.categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </div>

        {content.categories.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {content.categories.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => set('category', c)}
                className={cn(
                  'rounded-md px-2 py-1 text-xs ring-1 ring-inset transition',
                  form.category === c
                    ? 'bg-accsoft font-medium text-acc ring-line'
                    : 'text-txmuted ring-line hover:bg-paper hover:text-tx',
                )}
              >
                {c}
              </button>
            ))}
          </div>
        )}

        <FieldInput
          label="Short description"
          value={form.short_description}
          onChange={(e) => set('short_description', e.target.value)}
          hint="One line. This is what the bot replies with in a chat."
        />

        <FieldTextarea
          label="Full description"
          rows={4}
          value={form.description}
          onChange={(e) => set('description', e.target.value)}
          hint="The detail the bot reads out when a customer asks for more."
        />

        <div className="grid gap-4 sm:grid-cols-2">
          {content.priceLabel && (
            <FieldInput
              label={content.priceLabel}
              value={form.price}
              onChange={(e) => set('price', e.target.value)}
              hint="Optional — leave blank if it is not a fixed price."
              prefix="₹"
            />
          )}
          <FieldInput
            label="Short label"
            value={form.short_label}
            onChange={(e) => set('short_label', e.target.value)}
            hint="Shown as a small tag, e.g. “Popular”."
          />
          <FieldInput
            label="Link"
            value={form.detail_url}
            onChange={(e) => set('detail_url', e.target.value)}
            placeholder="https://…"
          />
        </div>

        {content.extraFields.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-3">
            {content.extraFields.map((field) => (
              <FieldInput
                key={field.key}
                label={field.label}
                value={form.attrs[field.key] ?? ''}
                onChange={(e) => set('attrs', { ...form.attrs, [field.key]: e.target.value })}
                placeholder={field.placeholder}
              />
            ))}
          </div>
        )}

        <Toggle
          checked={form.is_active}
          onChange={(v) => set('is_active', v)}
          label="Visible to the bot"
          description="Turn off to keep the record but stop the bot offering it."
        />

        <div className="flex items-center gap-2">
          <PrimaryButton disabled={busy} onClick={submit}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {initial ? 'Save changes' : `Add ${content.singular}`}
          </PrimaryButton>
          <GhostButton onClick={onCancel}>
            Cancel
          </GhostButton>
        </div>
      </div>
    </Panel>
  )
}
