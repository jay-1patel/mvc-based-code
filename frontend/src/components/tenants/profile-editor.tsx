import { useEffect, useMemo, useState } from 'react'
import { Check, Loader2, Pencil, RotateCcw, Save, Send, TriangleAlert } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { tenantsApi } from '@/lib/tenants'
import { editorContactErrors, editorFromProfile, editorToSnapshot, isValidPhone, timezoneOptions, type EditorForm } from '@/lib/profile'
import { FEATURE_LABELS, getVertical } from '@/lib/verticals'
import type { FeatureFlag } from '@/lib/types'
import {
  ErrorBanner,
  FieldInput,
  FieldSelect,
  FieldTextarea,
  GhostButton,
  Notice,
  Panel,
  PrimaryButton,
  TagField,
  Toggle,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/components/ui/toast'
import { useTenantDetail } from '@/components/tenants/hooks'

const FEATURE_GROUPS: { title: string; flags: FeatureFlag[] }[] = [
  {
    title: 'Selling & Orders',
    flags: ['cart', 'buy_now', 'orders', 'payments', 'track_order', 'returns', 'distributors'],
  },
  {
    title: 'Offerings & Catalogue',
    flags: ['offerings', 'offering_details', 'services', 'products', 'products_schemes', 'brochure_pdf'],
  },
  {
    title: 'Enquiries & Lead Gen',
    flags: ['lead_capture', 'quote', 'callback', 'book_appointment'],
  },
  {
    title: 'Knowledge Base & Support',
    flags: ['faq', 'kb', 'complaints', 'campaigns'],
  },
  {
    title: 'Human Handover',
    flags: ['handoff', 'human_handover'],
  },
]

/** The compact size the legacy `size="sm"` buttons had in the header. */
const smallBtn = 'px-3 py-1.5 text-xs'
const fieldLabelClass = 'mb-1.5 block text-sm font-medium text-tx'

export function ProfileEditor({ tenantId }: { tenantId: string }) {
  const detail = useTenantDetail(tenantId)
  const { identity } = useAuth()
  const isSuperAdmin = identity?.role === 'super_admin'
  // Decided role rule (flipped from the old model): the tenant's own admin
  // edits the profile here — saving writes a draft and publishing routes
  // through super-admin approval for non-menu changes. The super admin is
  // VIEW-ONLY inside a tenant, so for them this page is a read-only mirror.
  const readOnly = isSuperAdmin
  const action = useAction()
  const toast = useToast()
  const [form, setForm] = useState<EditorForm | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  // The profile is read-only until the admin explicitly opts in, so a stray
  // click on a switch or input cannot silently rewrite a live tenant.
  const [editing, setEditing] = useState(false)

  // Prefer the pending draft (merged over the file baseline): it contains
  // every unpublished change from the register wizard, so this editor and
  // the wizard always edit the same working copy.
  const effective = detail.data?.pending ?? detail.data?.effective ?? null
  const baseline = useMemo(
    () => (effective ? JSON.stringify(editorToSnapshot(editorFromProfile(effective))) : ''),
    [effective],
  )

  useEffect(() => {
    if (effective) {
      setForm(editorFromProfile(effective))
      setDirty(false)
    }
  }, [effective])

  if (detail.loading) return <LoadingBlock label="Loading the profile…" />
  if (detail.error) {
    return <ErrorBanner title="Could not load the profile" message={detail.error} />
  }
  if (!form || !effective) {
    return (
      <Notice tone="warn" title="No effective profile">
        The merged profile could not be built for this tenant. Fix the failing layer, or publish a valid draft
        from the versions screen.
      </Notice>
    )
  }

  const vertical = getVertical(effective.vertical)
  const patch = (values: Partial<EditorForm>) => {
    if (!editing) return
    setForm((f) => (f ? { ...f, ...values } : f))
    setDirty(true)
  }
  const current = JSON.stringify(editorToSnapshot(form))

  const saveDraft = async (thenPublish: boolean) => {
    const contactErrors = editorContactErrors(form)
    if (contactErrors.length) {
      setWarnings(contactErrors)
      toast.push('Fix the contact details before saving.', 'error')
      return
    }
    const saved = await action.run(() => tenantsApi.saveDraft(tenantId, editorToSnapshot(form)))
    if (!saved) return
    setWarnings(saved.validation ?? [])
    setDirty(false)
    if (!thenPublish) {
      toast.push('Draft saved. Nothing is live until you publish.')
      detail.reload()
      return
    }
    if ((saved.validation ?? []).length) {
      toast.push('Draft has validation warnings — review them before publishing.', 'error')
      setWarnings(saved.validation)
      return
    }
    const published = await action.run(() => tenantsApi.publish(tenantId))
    if (published) {
      if (published.status === 'pending_approval') {
        toast.push(published.message ?? 'Change sent for super admin approval.')
      } else {
        toast.push(`Published version ${published.version}`)
      }
      setEditing(false)
      detail.reload()
    }
  }

  return (
    <div>
      <PageHeader
        title="Profile"
        description={
          isSuperAdmin
            ? "View-only. This is the tenant's data layer as their admin maintains it — changes come from the tenant's admin and are approved by you."
            : 'This form is your data layer. Saving writes a draft; publishing validates it and swaps the live profile (profile-wide changes go to a super admin for approval first). Publishing is the only thing the bot ever sees.'
        }
        actions={
          readOnly ? undefined : editing ? (
            <>
              <GhostButton
                className={smallBtn}
                disabled={!dirty}
                onClick={() => {
                  setForm(editorFromProfile(effective))
                  setDirty(false)
                  setWarnings([])
                }}
              >
                <RotateCcw className="h-4 w-4" />
                Discard changes
              </GhostButton>
              {isSuperAdmin ? (
                <>
                  <GhostButton
                    className={smallBtn}
                    disabled={action.busy}
                    onClick={() => saveDraft(false)}
                  >
                    {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                    Save draft
                  </GhostButton>
                  <PrimaryButton
                    className={smallBtn}
                    disabled={action.busy}
                    onClick={() => saveDraft(true)}
                  >
                    {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    Save & publish
                  </PrimaryButton>
                </>
              ) : (
                <PrimaryButton
                  className={smallBtn}
                  disabled={action.busy}
                  onClick={() => saveDraft(true)}
                >
                  {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Submit for approval
                </PrimaryButton>
              )}
              <span title={dirty ? 'Save or discard your changes first' : undefined} className="inline-flex">
                <GhostButton
                  className={smallBtn}
                  disabled={dirty}
                  onClick={() => setEditing(false)}
                >
                  <Check className="h-4 w-4" />
                  Done editing
                </GhostButton>
              </span>
            </>
          ) : (
            <PrimaryButton
              className={smallBtn}
              onClick={() => setEditing(true)}
            >
              <Pencil className="h-4 w-4" />
              Edit profile
            </PrimaryButton>
          )
        }
        meta={
          <>
            <span className={`text-xs ${dirty ? 'text-attink' : 'text-txmuted'}`}>
              {readOnly
                ? 'View-only — changes come from the tenant\'s admin'
                : !editing
                  ? 'Read-only — click Edit profile to make changes'
                  : dirty
                    ? 'Unsaved changes'
                    : 'In sync with the live profile'}
            </span>
            <span className="text-xs text-txfaint">v{effective.vertical} · {vertical.label}</span>
          </>
        }
      />

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}
      {warnings.length > 0 && (
        <Notice tone="warn" title="Validation warnings" className="mb-4">
          <ul className="list-disc space-y-1 pl-4">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Brand">
          <p className="-mt-1 mb-4 text-sm text-txmuted">Names and contact details used in menus and signatures.</p>
          <div className="space-y-4">
            <FieldInput label="Display name" disabled={!editing} value={form.displayName} onChange={(e) => patch({ displayName: e.target.value })} />
            <FieldInput label="Company name" disabled={!editing} value={form.brand.name} onChange={(e) => patch({ brand: { ...form.brand, name: e.target.value } })} />
            <FieldInput label="Bot name" disabled={!editing} value={form.brand.botName} onChange={(e) => patch({ brand: { ...form.brand, botName: e.target.value } })} />
            <FieldInput label="Tagline" disabled={!editing} value={form.brand.tagline} onChange={(e) => patch({ brand: { ...form.brand, tagline: e.target.value } })} />
            <FieldInput label="Website" disabled={!editing} value={form.brand.website} onChange={(e) => patch({ brand: { ...form.brand, website: e.target.value } })} />
            <FieldInput
              label="Support email"
              type="email"
              disabled={!editing}
              value={form.brand.supportEmail}
              onChange={(e) => patch({ brand: { ...form.brand, supportEmail: e.target.value } })}
            />
            <FieldInput
              label="Support phone"
              disabled={!editing}
              value={form.brand.supportPhone}
              error={
                editing && form.brand.supportPhone.trim() && !isValidPhone(form.brand.supportPhone)
                  ? 'Phone must be a country code followed by a 10-digit number, e.g. +91 98765 43210'
                  : undefined
              }
              onChange={(e) => patch({ brand: { ...form.brand, supportPhone: e.target.value } })}
            />
            <FieldInput
              label="Closing signature"
              disabled={!editing}
              value={form.brand.signature}
              onChange={(e) => patch({ brand: { ...form.brand, signature: e.target.value } })}
            />
          </div>
        </Panel>

        <Panel title="Vocabulary">
          <p className="-mt-1 mb-4 text-sm text-txmuted">
            The nouns the bot uses. Changing these changes every message, menu and prompt that uses them.
          </p>
          <div className="space-y-4">
            {(
              [
                ['item_noun', 'Plural offering noun', 'products'],
                ['item_noun_singular', 'Singular offering noun', 'product'],
                ['browse_label', 'Browse label', 'Browse Products'],
                ['catalog_label', 'Brochure label', 'View Brochure'],
                ['lead_noun', 'Enquiry noun', 'enquiry'],
                ['enquiry_label', 'Enquiry button label', 'Enquire'],
                ['support_label', 'Support label', 'Support'],
              ] as const
            ).map(([key, label, example]) => (
              <FieldInput
                key={key}
                label={label}
                disabled={!editing}
                value={form.vocabulary[key] ?? ''}
                placeholder={example}
                onChange={(e) => patch({ vocabulary: { ...form.vocabulary, [key]: e.target.value } })}
              />
            ))}
            <div>
              <p className={fieldLabelClass}>Domain keywords</p>
              <p className="mb-2 text-xs text-txfaint">
                Comma separated. The classifier will not route anything to an intent that uses a term this profile switched off.
              </p>
              <TagField
                disabled={!editing}
                value={form.keywords.split(',').map((s) => s.trim()).filter(Boolean)}
                onChange={(next) => patch({ keywords: next.join(', ') })}
              />
            </div>
          </div>
        </Panel>

        <Panel title="Working hours">
          <p className="-mt-1 mb-4 text-sm text-txmuted">Working hours configuration.</p>
          <div className="space-y-4">
            <Toggle
              checked={form.businessHours.alwaysOpen}
              disabled={!editing}
              onChange={(alwaysOpen) => patch({ businessHours: { ...form.businessHours, alwaysOpen } })}
              label="Always open"
            />
            <div className="grid grid-cols-3 gap-3">
              <FieldSelect
                label="Timezone"
                disabled={!editing}
                value={form.businessHours.timezone}
                onChange={(e) => patch({ businessHours: { ...form.businessHours, timezone: e.target.value } })}
              >
                <option value="">Select…</option>
                {timezoneOptions(form.businessHours.timezone).map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </FieldSelect>
              <FieldInput
                label="Opens"
                type="time"
                disabled={!editing || form.businessHours.alwaysOpen}
                value={form.businessHours.open}
                onChange={(e) => patch({ businessHours: { ...form.businessHours, open: e.target.value } })}
              />
              <FieldInput
                label="Closes"
                type="time"
                disabled={!editing || form.businessHours.alwaysOpen}
                value={form.businessHours.close}
                onChange={(e) => patch({ businessHours: { ...form.businessHours, close: e.target.value } })}
              />
            </div>
            <FieldTextarea
              label="Out-of-hours message"
              rows={2}
              disabled={!editing}
              value={form.businessHours.outOfHoursMessage}
              onChange={(e) => patch({ businessHours: { ...form.businessHours, outOfHoursMessage: e.target.value } })}
            />
          </div>
        </Panel>

        <Panel title="Notifications">
          <p className="-mt-1 mb-4 text-sm text-txmuted">Where leads, quotes and handoffs are pushed.</p>
          <div className="space-y-4">
            <FieldInput
              label="Sales email"
              type="email"
              disabled={!editing}
              value={form.notifications.salesEmail}
              onChange={(e) => patch({ notifications: { ...form.notifications, salesEmail: e.target.value } })}
            />
            <FieldInput
              label="Support email"
              type="email"
              disabled={!editing}
              value={form.notifications.supportEmail}
              onChange={(e) => patch({ notifications: { ...form.notifications, supportEmail: e.target.value } })}
            />
          </div>
        </Panel>
      </div>

      <Panel title="Features & Capabilities" className="mt-4">
        <p className="-mt-1 mb-4 text-sm text-txmuted">
          Enable or disable capabilities for this tenant. Enabled features activate WhatsApp menus, intents, and console panels.
        </p>
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {FEATURE_GROUPS.map((group) => (
            <div key={group.title} className="rounded-panel border border-line bg-paper p-4">
              <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-txmuted">
                {group.title}
              </h4>
              <div className="space-y-3">
                {group.flags.map((flag) => {
                  const meta = FEATURE_LABELS[flag]
                  const checked = form.features.includes(flag)
                  return (
                    <div key={flag} className="flex items-start justify-between gap-2 pt-1">
                      <div className="min-w-0 pr-2">
                        <p className="text-sm font-medium text-tx">{meta?.label ?? flag}</p>
                        <p className="text-xs text-txmuted">{meta?.help ?? ''}</p>
                      </div>
                      <Toggle
                        disabled={!editing}
                        checked={checked}
                        onChange={(on) => {
                          const next = on
                            ? [...form.features.filter((f) => f !== flag), flag]
                            : form.features.filter((f) => f !== flag)
                          patch({ features: next })
                        }}
                      />
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Guardrails" className="mt-4">
        <p className="-mt-1 mb-4 text-sm text-txmuted">
          What the bot must never claim, and when it must stop and call a human.
        </p>
        <div className="space-y-4">
          <FieldTextarea
            label="Never state (one per line)"
            rows={4}
            disabled={!editing}
            value={form.guardrails.neverState}
            onChange={(e) => patch({ guardrails: { ...form.guardrails, neverState: e.target.value } })}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className={fieldLabelClass}>Forbidden terms</p>
              <TagField
                disabled={!editing}
                value={split(form.guardrails.forbiddenTerms)}
                onChange={(next) => patch({ guardrails: { ...form.guardrails, forbiddenTerms: next.join(', ') } })}
              />
            </div>
            <div>
              <p className={fieldLabelClass}>Handoff keywords</p>
              <TagField
                disabled={!editing}
                value={split(form.guardrails.handoffKeywords)}
                onChange={(next) => patch({ guardrails: { ...form.guardrails, handoffKeywords: next.join(', ') } })}
              />
            </div>
          </div>
          <div>
            <p className={fieldLabelClass}>Escalate on</p>
            <TagField
              disabled={!editing}
              value={split(form.guardrails.escalateKeywords)}
              onChange={(next) => patch({ guardrails: { ...form.guardrails, escalateKeywords: next.join(', ') } })}
            />
          </div>
          <FieldInput
            label="Escalation message"
            disabled={!editing}
            value={form.guardrails.escalationMessage}
            onChange={(e) => patch({ guardrails: { ...form.guardrails, escalationMessage: e.target.value } })}
          />
        </div>
      </Panel>

      <p className="mt-4 flex items-center gap-2 text-xs text-txmuted">
        <TriangleAlert className="h-3.5 w-3.5" />
        {current === baseline
          ? 'No changes yet.'
          : isSuperAdmin
            ? 'Unsaved changes will be written as a draft, not published.'
            : 'Unsaved changes stay a draft until you submit them for approval.'}
      </p>
    </div>
  )
}

function split(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
}
