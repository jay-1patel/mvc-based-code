import { useMemo, useState, type ChangeEvent } from 'react'
import {
  AlertTriangle,
  Loader2,
  Lock,
  Pencil,
  Plus,
  Search,
  Settings2,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import { contentWritePerms, usePermissions } from '@/lib/permissions'
import { notifyOfferingsChanged, offeringsApi } from '@/lib/offerings'
import { uploadRecordImage } from '@/lib/uploads'
import { useTenants } from '@/lib/tenants'
import { getVertical } from '@/lib/verticals'
import type { Offering, OfferingInput, RecordColumn, RecordColumnType } from '@/lib/types'
import { cn } from '@/lib/cn'
import {
  Check,
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
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

/** Mirrors `shared/tenancy/records.py` COLUMN_TYPES for the column editor. */
const COLUMN_TYPE_OPTIONS: { value: RecordColumnType; label: string; hint: string }[] = [
  { value: 'text', label: 'Text', hint: 'Short free text.' },
  { value: 'long_text', label: 'Long text', hint: 'Multi-line, e.g. an itinerary.' },
  { value: 'number', label: 'Number', hint: 'Any number, stored with decimals.' },
  { value: 'integer', label: 'Whole number', hint: 'Counts, days, max travellers.' },
  { value: 'boolean', label: 'Yes / no', hint: 'A tick box.' },
  { value: 'date', label: 'Date', hint: 'YYYY-MM-DD.' },
  { value: 'select', label: 'Pick from a list', hint: 'You supply the choices.' },
]

/** System columns map straight onto the offering record; the rest go in `values`. */
const SYSTEM_FIELDS = new Set([
  'name',
  'category',
  'short_label',
  'short_description',
  'description',
  'price',
  'detail_url',
  'media_url',
  'is_active',
])

function cellValue(row: Offering, column: RecordColumn): string | number | boolean | null {
  if (SYSTEM_FIELDS.has(column.key)) {
    const value = (row as unknown as Record<string, unknown>)[column.key]
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
    return null
  }
  return row.values?.[column.key] ?? null
}

function displayCell(value: string | number | boolean | null): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function publicImageUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  try {
    const url = new URL(value.trim())
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

/**
 * The tenant's records, as a table whose columns the admin owns.
 *
 * Every vertical gets a different table — SKU and size for a shop, itinerary and
 * max travellers for a travel company. Adding a column here is a real database
 * change (`ALTER TABLE products`), which is why schema edits need
 * `manage_operations` while day-to-day row edits need only
 * `edit_delete_products`.
 */
export function RecordsPanel({ tenantId }: { tenantId: string }) {
  const { active } = useTenants()
  const { can } = useAuth()
  const { canWriteTenant } = usePermissions()
  const toast = useToast()
  const action = useAction()

  const content = getVertical(active?.vertical).content
  const canRead = can('view_products')
  // Add/edit/remove are granted separately per vertical — see contentWritePerms.
  // canWriteTenant is false for a super admin: those writes are refused by the API.
  const { add: addPerms, edit: editPerms, remove: removePerms } = contentWritePerms(active?.vertical)
  const canAdd = addPerms.some((perm) => canWriteTenant(perm))
  const canEditRow = editPerms.some((perm) => canWriteTenant(perm))
  const canRemove = removePerms.some((perm) => canWriteTenant(perm))
  const canEdit = canAdd || canEditRow || canRemove
  const canSchema = canWriteTenant('manage_operations')

  const [showInactive, setShowInactive] = useState(false)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')

  const state = useAsync(
    (signal) => offeringsApi.listDetailed(tenantId, { includeInactive: showInactive }, signal),
    [tenantId, showInactive],
  )
  const columns = state.data?.columns ?? []
  const rows = state.data?.offerings ?? []

  const [editing, setEditing] = useState<Offering | null>(null)
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const [columnForm, setColumnForm] = useState<{ column: RecordColumn | null } | null>(null)
  const [uploadingImageFor, setUploadingImageFor] = useState<number | null>(null)

  const categories = useMemo(
    () => [...new Set(rows.map((r) => r.category).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [rows],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter((row) => {
      if (category && row.category !== category) return false
      if (!q) return true
      const haystack = [
        row.name,
        row.category,
        row.short_description,
        ...Object.values(row.values ?? {}).map((v) => (v == null ? '' : String(v))),
      ]
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [rows, search, category])

  const reload = state.reload

  const uploadImageForRow = async (row: Offering, file?: File) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast.push('Only image files (PNG, JPG, WebP…) can be uploaded.', 'error')
      return
    }

    setUploadingImageFor(row.id)
    try {
      const url = await uploadRecordImage(tenantId, file)
      const updated = await action.run(() =>
        offeringsApi.update(tenantId, row.id, { media_url: url, media_type: 'image' }),
      )
      if (updated) {
        reload()
        notifyOfferingsChanged(tenantId)
        toast.push(`Image saved for ${row.name}`)
      }
    } catch (err) {
      toast.push(err instanceof Error ? err.message : 'The image upload failed.', 'error')
    } finally {
      setUploadingImageFor(null)
    }
  }

  const removeRow = async (id: number, hard: boolean) => {
    const result = await action.run(() => offeringsApi.remove(tenantId, id, hard))
    if (result) {
      setConfirmDelete(null)
      reload()
      notifyOfferingsChanged(tenantId)
      toast.push(hard ? 'Deleted permanently' : 'Removed — the bot will not list it again')
    }
  }

  const dropColumn = async (column: RecordColumn) => {
    const result = await action.run(() => offeringsApi.deleteColumn(tenantId, column.key))
    if (result) {
      toast.push(`‘${column.label}’ ${result.database_column_dropped ? 'and its column were' : 'was'} removed`)
      reload()
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
        description={`A table the bot reads from. Its columns are this tenant's own — add a ${content.singular} column the business needs and the bot can answer with it.`}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {canRead && (
              <GhostButton disabled={!canSchema} onClick={() => setColumnForm({ column: null })}>
                <Settings2 className="h-4 w-4" />
                Add column
              </GhostButton>
            )}
            {canAdd && (
              <PrimaryButton disabled={creating || editing !== null} onClick={() => setCreating(true)}>
                <Plus className="h-4 w-4" />
                Add a {content.singular}
              </PrimaryButton>
            )}
          </div>
        }
      />

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}

      {columnForm && (
        <ColumnForm
          initial={columnForm.column}
          busy={action.busy}
          onCancel={() => setColumnForm(null)}
          onSubmit={async (body) => {
            const result = columnForm.column
              ? await action.run(() =>
                  offeringsApi.updateColumn(tenantId, columnForm.column!.key, body),
                )
              : await action.run(() => offeringsApi.createColumn(tenantId, body))
            if (result) {
              setColumnForm(null)
              reload()
              toast.push(columnForm.column ? 'Column updated' : 'Column added')
            }
          }}
        />
      )}

      {creating && (
        <RecordForm
          tenantId={tenantId}
          columns={columns}
          content={content}
          busy={action.busy}
          onCancel={() => setCreating(false)}
          onSubmit={async (body) => {
            const result = await action.run(() => offeringsApi.create(tenantId, body))
            if (result) {
              setCreating(false)
              reload()
              notifyOfferingsChanged(tenantId)
              toast.push(`${result.name} added`)
            }
          }}
        />
      )}

      {editing && (
        <RecordForm
          tenantId={tenantId}
          columns={columns}
          content={content}
          initial={editing}
          busy={action.busy}
          onCancel={() => setEditing(null)}
          onSubmit={async (body) => {
            const result = await action.run(() => offeringsApi.update(tenantId, editing.id, body))
            if (result) {
              setEditing(null)
              reload()
              notifyOfferingsChanged(tenantId)
              toast.push('Saved')
            }
          }}
        />
      )}

      <Panel
        className="mt-4"
        title={`${filtered.length} ${filtered.length === 1 ? content.singular : content.label.toLowerCase()}`}
        action={
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex w-48 max-w-full items-center gap-2 rounded-lg border border-line bg-inputbg px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 shrink-0 text-txfaint" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search…"
                aria-label="Search"
                className="w-full bg-transparent text-xs text-tx outline-none placeholder:text-txfaint"
              />
            </label>
            {categories.length > 1 && (
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                aria-label="Group"
                className="max-w-full rounded-lg border border-line bg-inputbg px-2 py-1.5 text-xs text-tx outline-none focus:border-acc"
              >
                <option value="">All groups</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
            <Toggle size="sm" checked={showInactive} onChange={setShowInactive} label="Show removed" />
          </div>
        }
      >
        {state.loading ? (
          <LoadingBlock label={`Loading ${content.label.toLowerCase()}…`} />
        ) : state.error ? (
          <ErrorBanner title="Could not load" message={state.error} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={`No ${content.label.toLowerCase()} yet`}
            description={
              canEdit
                ? `Add the first ${content.singular} and the bot can start answering questions about it.`
                : 'Nothing has been added for this tenant yet.'
            }
            action={
              canSchema ? (
                <GhostButton onClick={() => setColumnForm({ column: null })}>
                  <Settings2 className="h-4 w-4" />
                  Shape the columns
                </GhostButton>
              ) : undefined
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-left text-sm">
              <thead>
                <tr className="border-b border-line">
                  {columns.map((column) => (
                    <th key={column.key} className="whitespace-nowrap px-4 py-2.5 align-bottom">
                      <div className="flex items-center gap-1.5">
                        <span className="text-2xs font-semibold uppercase tracking-wider text-txmuted">
                          {column.label}
                        </span>
                        {column.is_system ? (
                          <Lock className="h-3 w-3 text-txfaint" />
                        ) : canSchema ? (
                          <span className="flex items-center">
                            <button
                              type="button"
                              title="Edit column"
                              onClick={() => setColumnForm({ column })}
                              className="rounded p-0.5 text-txmuted transition hover:bg-paper hover:text-tx"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                            <button
                              type="button"
                              title="Delete column and its data"
                              onClick={() => dropColumn(column)}
                              className="rounded p-0.5 text-txmuted transition hover:bg-badbg hover:text-badink"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </span>
                        ) : null}
                      </div>
                    </th>
                  ))}
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-linesoft">
                {filtered.map((row) => (
                  <tr key={row.id} className="align-top">
                    {columns.map((column) => {
                      const imageUrl =
                        column.key === 'media_url' ? publicImageUrl(cellValue(row, column)) : null
                      return (
                        <td key={column.key} className="px-4 py-3 text-tx">
                          {column.key === 'name' ? (
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-medium">{row.name}</span>
                              {!row.is_active && <Pill>removed</Pill>}
                            </div>
                          ) : column.key === 'short_label' && row.short_label ? (
                            <Pill tone="ok">{row.short_label}</Pill>
                          ) : column.key === 'media_url' ? (
                            <div className="flex min-w-36 items-center gap-2">
                              {imageUrl ? (
                                <a
                                  href={imageUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  title={imageUrl}
                                  className="flex min-w-0 items-center gap-2 text-acc hover:underline"
                                >
                                  <img
                                    src={imageUrl}
                                    alt=""
                                    className="h-8 w-8 shrink-0 rounded border border-line object-cover"
                                  />
                                  <span className="max-w-40 truncate text-xs">{imageUrl}</span>
                                </a>
                              ) : (
                                <span className="text-txfaint">—</span>
                              )}
                              {canEditRow && (
                                <label
                                  className={`inline-flex cursor-pointer items-center gap-1 whitespace-nowrap rounded px-2 py-1 text-xs text-acc hover:bg-accsoft ${
                                    uploadingImageFor === row.id ? 'pointer-events-none opacity-60' : ''
                                  }`}
                                  title={imageUrl ? 'Replace image' : 'Add image'}
                                >
                                  <input
                                    type="file"
                                    accept="image/*"
                                    className="sr-only"
                                    disabled={uploadingImageFor === row.id}
                                    onChange={(event) => {
                                      const file = event.target.files?.[0]
                                      event.target.value = ''
                                      void uploadImageForRow(row, file)
                                    }}
                                  />
                                  <Upload className="h-3.5 w-3.5" />
                                  {uploadingImageFor === row.id
                                    ? 'Uploading…'
                                    : imageUrl
                                      ? 'Replace'
                                      : 'Add image'}
                                </label>
                              )}
                            </div>
                          ) : (
                            <span className={cn(column.type === 'long_text' && 'block max-w-xs truncate')}>
                              {displayCell(cellValue(row, column))}
                            </span>
                          )}
                        </td>
                      )
                    })}
                    <td className="px-4 py-3">
                      {(canEditRow || canRemove) && (
                        <div className="flex shrink-0 items-center justify-end gap-2">
                          {canEditRow && (
                            <GhostButton className="px-2.5 py-1.5 text-xs" onClick={() => setEditing(row)}>
                              <Pencil className="h-3.5 w-3.5" /> Edit
                            </GhostButton>
                          )}
                          {canRemove && row.is_active &&
                            (confirmDelete === row.id ? (
                              <>
                                <DangerButton
                                  className="px-2.5 py-1.5 text-xs"
                                  disabled={action.busy}
                                  onClick={() => removeRow(row.id, true)}
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
                                onClick={() => setConfirmDelete(row.id)}
                              >
                                <Trash2 className="h-3.5 w-3.5" /> Remove
                              </GhostButton>
                            ))}
                          {canEditRow && !row.is_active && (
                            <GhostButton
                              className="px-2.5 py-1.5 text-xs"
                              disabled={action.busy}
                              onClick={() =>
                                action
                                  .run(() => offeringsApi.update(tenantId, row.id, { is_active: true }))
                                  .then((r) => r && reload())
                              }
                            >
                              {action.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                              Restore
                            </GhostButton>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {canSchema && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-txmuted">
          <AlertTriangle className="h-3.5 w-3.5" />
          Deleting a column drops its data. Core columns (name, price, …) are always kept.
        </p>
      )}
    </div>
  )
}

// ── column editor ───────────────────────────────────────────────────────────

function ColumnForm({
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  initial: RecordColumn | null
  busy: boolean
  onSubmit: (body: {
    key: string
    label: string
    type: RecordColumnType
    required: boolean
    options: string[]
    help: string
  }) => void
  onCancel: () => void
}) {
  const [key, setKey] = useState(initial?.key ?? '')
  const [label, setLabel] = useState(initial?.label ?? '')
  const [type, setType] = useState<RecordColumnType>(initial?.type ?? 'text')
  const [required, setRequired] = useState(initial?.required ?? false)
  const [options, setOptions] = useState((initial?.options ?? []).join(', '))
  const [help, setHelp] = useState(initial?.help ?? '')

  const typeHint = COLUMN_TYPE_OPTIONS.find((t) => t.value === type)?.hint

  return (
    <Panel
      className="mt-4"
      title={initial ? `Edit the ‘${initial.label}’ column` : 'Add a column'}
      action={
        <GhostButton className="px-2.5 py-1.5 text-xs" onClick={onCancel}>
          <X className="h-3.5 w-3.5" /> Close
        </GhostButton>
      }
    >
      <p className="-mt-1 mb-4 text-sm text-txmuted">
        A column is a field on every record — a SKU, a destination, a duration.
      </p>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <FieldInput
            label="Column name"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Destination"
            hint="The header the admin and the bot see."
          />
          <FieldInput
            label="Key"
            value={key}
            disabled={Boolean(initial)}
            onChange={(e) => setKey(e.target.value)}
            placeholder="destination"
            hint="lower-case, underscores; cannot be changed later."
          />
        </div>

        <FieldSelect
          label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as RecordColumnType)}
          hint={typeHint}
        >
          {COLUMN_TYPE_OPTIONS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </FieldSelect>

        {type === 'select' && (
          <FieldInput
            label="Choices"
            value={options}
            onChange={(e) => setOptions(e.target.value)}
            placeholder="Fixed price, Time and materials, Retainer"
            hint="Comma-separated."
          />
        )}

        <FieldInput label="Help text" value={help} onChange={(e) => setHelp(e.target.value)} placeholder="Optional note shown under the field." />

        <Check checked={required} onChange={setRequired} label="Required" description="A record cannot be saved without it." />

        <div className="flex items-center gap-2">
          <PrimaryButton
            disabled={busy}
            onClick={() =>
              onSubmit({
                key: (initial?.key ?? key).trim().toLowerCase(),
                label: label.trim() || key.trim(),
                type,
                required,
                options: options
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
                help: help.trim(),
              })
            }
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {initial ? 'Save column' : 'Add column'}
          </PrimaryButton>
          <GhostButton onClick={onCancel}>
            Cancel
          </GhostButton>
        </div>
      </div>
    </Panel>
  )
}

// ── record editor ───────────────────────────────────────────────────────────

type Cells = Record<string, string | boolean>

function initialCells(columns: RecordColumn[], row?: Offering): Cells {
  const cells: Cells = {}
  for (const column of columns) {
    if (column.type === 'boolean') {
      // A new record defaults to visible; every other boolean column
      // (returnable, includes_flight…) defaults to off.
      cells[column.key] = Boolean(row ? cellValue(row, column) : column.key === 'is_active')
    } else {
      const value = row ? cellValue(row, column) : null
      cells[column.key] =
        column.key === 'media_url'
          ? publicImageUrl(value) ?? ''
          : value === null || value === undefined
            ? ''
            : String(value)
    }
  }
  return cells
}

function RecordForm({
  tenantId,
  columns,
  content,
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  tenantId: string
  columns: RecordColumn[]
  content: ReturnType<typeof getVertical>['content']
  initial?: Offering
  busy: boolean
  onSubmit: (body: OfferingInput) => void
  onCancel: () => void
}) {
  const [cells, setCells] = useState<Cells>(() => initialCells(columns, initial))
  const [touched, setTouched] = useState(false)
  const [imageBusy, setImageBusy] = useState(false)
  const [imageError, setImageError] = useState<string | null>(null)

  const set = (key: string, value: string | boolean) => setCells((c) => ({ ...c, [key]: value }))
  const nameColumn = columns.find((c) => c.key === 'name')
  const nameMissing = touched && nameColumn && !String(cells.name ?? '').trim()

  const pickImage = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!file.type.startsWith('image/')) {
      setImageError('Only image files (PNG, JPG, WebP…) can be uploaded.')
      return
    }
    setImageBusy(true)
    setImageError(null)
    try {
      const url = await uploadRecordImage(tenantId, file)
      set('media_url', url)
    } catch (err) {
      setImageError(err instanceof Error ? err.message : 'The upload failed.')
    } finally {
      setImageBusy(false)
    }
  }

  const submit = () => {
    setTouched(true)
    if (nameMissing) return

    const top: Record<string, unknown> = {}
    const values: Record<string, string | boolean> = {}
    for (const column of columns) {
      const value = cells[column.key]
      if (SYSTEM_FIELDS.has(column.key)) top[column.key] = value
      else values[column.key] = value
    }

    onSubmit({
      name: String(top.name ?? '').trim(),
      category: String(top.category ?? '').trim() || 'general',
      short_label: String(top.short_label ?? '').trim(),
      short_description: String(top.short_description ?? '').trim(),
      description: String(top.description ?? '').trim(),
      price: String(top.price ?? '').trim() || null,
      detail_url: String(top.detail_url ?? '').trim(),
      media_url: String(top.media_url ?? '').trim(),
      media_type: 'image',
      attrs: initial?.attrs ?? {},
      values,
      sort_order: initial?.sort_order ?? 0,
      is_active: Boolean(top.is_active),
    })
  }

  return (
    <Panel
      className="mt-4"
      title={initial ? `Edit ${initial.name}` : `Add a ${content.singular}`}
      action={
        <GhostButton className="px-2.5 py-1.5 text-xs" onClick={onCancel}>
          <X className="h-3.5 w-3.5" /> Close
        </GhostButton>
      }
    >
      <p className="-mt-1 mb-4 text-sm text-txmuted">
        The fields are this tenant's columns — the same ones shown in the table.
      </p>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          {columns.map((column) => {
            const value = cells[column.key]
            if (column.type === 'long_text') {
              return (
                <div key={column.key} className="sm:col-span-2">
                  <FieldTextarea
                    label={column.label}
                    rows={3}
                    value={String(value ?? '')}
                    hint={column.help || undefined}
                    error={column.key === 'name' && nameMissing ? 'A name is required' : undefined}
                    onChange={(e) => set(column.key, e.target.value)}
                  />
                </div>
              )
            }
            if (column.type === 'boolean') {
              return (
                <div key={column.key} className="flex items-center pt-6">
                  <Check
                    checked={Boolean(value)}
                    onChange={(next) => set(column.key, next)}
                    label={column.label}
                    description={column.help || undefined}
                  />
                </div>
              )
            }
            if (column.type === 'select') {
              return (
                <FieldSelect
                  key={column.key}
                  label={column.label}
                  value={String(value ?? '')}
                  onChange={(e) => set(column.key, e.target.value)}
                  hint={column.help || undefined}
                >
                  <option value="">—</option>
                  {column.options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </FieldSelect>
              )
            }
            if (column.key === 'media_url') {
              const url = String(value ?? '')
              return (
                <div key={column.key}>
                  <span className="mb-1.5 block text-sm font-medium text-tx">{column.label}</span>
                  {url ? (
                    <div className="flex items-center gap-2.5 rounded-lg border border-line bg-inputbg p-2">
                      <img
                        src={url}
                        alt=""
                        className="h-10 w-10 shrink-0 rounded border border-line object-cover"
                      />
                      <span className="min-w-0 flex-1 truncate text-xs text-txmuted">{url}</span>
                      <GhostButton
                        className="px-2.5 py-1.5 text-xs"
                        onClick={() => set('media_url', '')}
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Remove
                      </GhostButton>
                    </div>
                  ) : (
                    <label
                      className={cn(
                        'flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-line bg-inputbg py-2 text-sm text-txmuted transition hover:border-acc hover:text-acc',
                        imageBusy && 'pointer-events-none opacity-60',
                      )}
                    >
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        disabled={imageBusy}
                        onChange={pickImage}
                      />
                      <Upload className="h-4 w-4" />
                      {imageBusy ? 'Uploading…' : 'Upload image'}
                    </label>
                  )}
                  {imageError && <p className="mt-1 text-xs text-badink">{imageError}</p>}
                  {column.help && !imageError && (
                    <p className="mt-1 text-xs text-txmuted">{column.help}</p>
                  )}
                </div>
              )
            }
            return (
              <FieldInput
                key={column.key}
                label={column.label}
                type={column.type === 'date' ? 'date' : column.type === 'number' || column.type === 'integer' ? 'number' : 'text'}
                value={String(value ?? '')}
                required={column.required && column.key !== 'name'}
                hint={column.help || undefined}
                error={column.key === 'name' && nameMissing ? 'A name is required' : undefined}
                onChange={(e) => set(column.key, e.target.value)}
              />
            )
          })}
        </div>

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
