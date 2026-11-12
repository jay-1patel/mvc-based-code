import { useState } from 'react'
import { Loader2, Pencil, Plus, Save, Send, Trash2, X } from 'lucide-react'
import { tenantsApi } from '@/lib/tenants'
import { useAction } from '@/lib/hooks'
import { FEATURE_FLAGS, type IntentSpec } from '@/lib/types'
import { FEATURE_LABELS } from '@/lib/verticals'
import {
  Card,
  FieldInput,
  FieldSelect,
  FieldTextarea,
  GhostButton,
  IconButton,
  Notice,
  Pill,
  PrimaryButton,
  TagField,
  Toggle,
} from '@/components/remake/ui'
import { Alert, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useConfirm } from '@/components/ui/confirm'
import { useTenantDetail } from '@/components/tenants/hooks'
import { usePermissions } from '@/lib/permissions'

const FIELD_LABEL = 'mb-1.5 block text-sm font-medium text-tx'

export function IntentsEditor({ tenantId }: { tenantId: string }) {
  const { data, loading, error, reload } = useTenantDetail(tenantId)
  const action = useAction()
  const toast = useToast()
  const confirmDialog = useConfirm()
  // Save/Publish are tenant-data writes: hidden unless the caller may
  // write this tenant (a super admin is view-only here).
  const { canWriteTenant } = usePermissions()
  const canWrite = canWriteTenant('manage_operations')
  const [draft, setDraft] = useState<IntentSpec | null>(null)
  const [editing, setEditing] = useState(false)
  const [validation, setValidation] = useState<string[]>([])

  // Draft-first: intents created from the menu editor land in the working
  // copy, so the editor must list the pending profile — not just what is
  // published — or a freshly added menu option has no editable intent here.
  const profile = data?.pending ?? data?.effective ?? null
  const publishedNames = new Set((data?.effective?.intents ?? []).map((i) => i.name))
  const all = profile?.intents ?? []

  const save = async (thenPublish: boolean) => {
    if (!draft) return
    const body: any = {
      answer: draft.answer ?? null,
      keywords: draft.keywords ?? [],
      examples: draft.examples ?? [],
      enabled: draft.enabled ?? true,
      flow: draft.flow ?? null,
      requires_feature: draft.requires_feature ?? null,
    }
    const saved = await action.run(() => tenantsApi.saveIntent(tenantId, draft.name, body))
    if (!saved) return
    setValidation(saved.validation ?? [])
    if (!thenPublish) {
      toast.push('Intent saved to draft.')
      reload()
      return
    }
    if ((saved.validation ?? []).length > 0) {
      toast.push('Draft has validation warnings — fix before publishing.')
      return
    }
    const pub = await action.run(() => tenantsApi.publish(tenantId))
    if (pub) {
      if (pub.status === 'pending_approval') {
        toast.push(pub.message ?? 'Change sent for approval.')
      } else {
        toast.push(`Published version ${pub.version}`)
      }
      reload()
      setDraft(null)
      setEditing(false)
    }
  }

  const remove = async (name: string) => {
    if (
      !(await confirmDialog({
        message: `Delete intent "${name}"?`,
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return
    const body: any = { __remove__: true }
    const saved = await action.run(() => tenantsApi.saveIntent(tenantId, name, body))
    if (!saved) return
    const pub = await action.run(() => tenantsApi.publish(tenantId))
    if (pub) {
      if (pub.status === 'pending_approval') toast.push(pub.message ?? 'Change sent for approval.')
      else toast.push(`Published version ${pub.version}`)
      reload()
    }
  }

  const startAdd = () => {
    setDraft({
      name: '',
      examples: [],
      keywords: [],
      requires_feature: null,
      flow: null,
      answer: '',
      enabled: true,
    })
    setEditing(true)
  }

  const startEdit = (i: IntentSpec) => {
    setDraft({ ...i, examples: [...(i.examples ?? [])], keywords: [...(i.keywords ?? [])] })
    setEditing(true)
  }

  if (loading) return <LoadingBlock label="Loading intents…" />
  if (error || !data) return <Alert tone="danger" title="Could not load intents">{error}</Alert>

  return (
    <div>
      <PageHeader
        title="Questions the bot understands"
        description="Add, edit, enable/disable intents. Changes go to draft and need publishing."
        actions={
          canWrite ? (
            <PrimaryButton onClick={startAdd}>
              <Plus className="h-4 w-4" />
              Add intent
            </PrimaryButton>
          ) : undefined
        }
      />

      {editing && draft && (
        <Card className="mb-4">
          <div className="flex items-center justify-between gap-3 border-b border-linesoft px-4 py-3 sm:px-5">
            <h2 className="text-sm font-semibold text-tx">
              {draft.name ? `Edit ${draft.name}` : 'Add new intent'}
            </h2>
            <IconButton
              title="Close"
              onClick={() => { setEditing(false); setDraft(null) }}
            >
              <X className="h-4 w-4" />
            </IconButton>
          </div>
          <div className="space-y-4 p-4 sm:p-5">
            <FieldInput label="Name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} disabled={Boolean(all.find((a) => a.name === draft.name) && !editing)} placeholder="intent_name" />
            <FieldTextarea label="Answer" rows={4} value={draft.answer ?? ''} onChange={(e) => setDraft({ ...draft, answer: e.target.value })} />
            <div>
              <span className={FIELD_LABEL}>Keywords</span>
              <TagField value={draft.keywords ?? []} onChange={(k) => setDraft({ ...draft, keywords: k })} placeholder="Keywords" />
            </div>
            <div>
              <span className={FIELD_LABEL}>Examples</span>
              <TagField value={draft.examples ?? []} onChange={(e) => setDraft({ ...draft, examples: e })} placeholder="Examples" />
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <FieldSelect label="Requires feature" value={draft.requires_feature ?? ''} onChange={(e) => setDraft({ ...draft, requires_feature: e.target.value || null })}>
                <option value="">None</option>
                {FEATURE_FLAGS.map((f) => (
                  <option key={f} value={f}>{FEATURE_LABELS[f]?.label ?? f}</option>
                ))}
              </FieldSelect>
              <FieldInput label="Flow" value={draft.flow ?? ''} onChange={(e) => setDraft({ ...draft, flow: e.target.value || '' })} placeholder="flow_name" />
              <div className="flex items-end pb-1">
                <Toggle checked={draft.enabled ?? true} onChange={(en) => setDraft({ ...draft, enabled: en })} label="Enabled" />
              </div>
            </div>
            <div className="flex gap-2">
              <PrimaryButton onClick={() => save(false)} disabled={action.busy}>
                {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                Save draft
              </PrimaryButton>
              <GhostButton onClick={() => save(true)} disabled={action.busy}>
                <Send className="h-4 w-4" />
                Save & publish
              </GhostButton>
            </div>
            {validation.length > 0 && <Notice tone="warn">{validation.join(', ')}</Notice>}
          </div>
        </Card>
      )}

      <Card>
        <div className="border-b border-linesoft px-4 py-3 sm:px-5">
          <h2 className="text-sm font-semibold text-tx">All intents</h2>
        </div>
        <div className="space-y-2 p-4 sm:p-5">
          {all.map((i) => (
            <div key={i.name} className="rounded-panel bg-paper p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-tx">{i.name}</span>
                  {!publishedNames.has(i.name) && <Pill tone="ok">draft</Pill>}
                  {i.enabled ? <Pill tone="ok">enabled</Pill> : <Pill>disabled</Pill>}
                  {i.flow && <Pill tone="ok">flow:{i.flow}</Pill>}
                  {i.requires_feature && <Pill>needs:{i.requires_feature}</Pill>}
                </div>
                {canWrite && (
                  <div className="flex gap-1">
                    <IconButton title="Edit" onClick={() => startEdit(i)}>
                      <Pencil className="h-4 w-4" />
                    </IconButton>
                    <IconButton title="Delete" danger onClick={() => remove(i.name)}>
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                  </div>
                )}
              </div>
              {i.keywords?.length > 0 && <p className="mt-1 text-xs text-txmuted">Keywords: {i.keywords.join(', ')}</p>}
              {i.examples?.length > 0 && <p className="mt-1 text-xs text-txmuted">Examples: {i.examples.join(', ')}</p>}
              {i.answer && <p className="mt-1 truncate text-xs text-txmuted">{i.answer}</p>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
