import { useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  Building2,
  CalendarClock,
  Check,
  FileText,
  FolderUp,
  Loader2,
  MessageSquareText,
  Rocket,
  ShieldAlert,
  Sparkles,
  Webhook,
} from 'lucide-react'
import {
  applyStagedToDraft,
  applyVertical,
  draftFromProfile,
  draftToSnapshot,
  EMAIL_RE,
  emptyDraft,
  isValidPhone,
  isValidPhoneInput,
  slugify,
  timezoneOptions,
  URL_RE,
  type WizardDraft,
} from '@/lib/profile'
import { onboardingKitsApi } from '@/lib/uploads'
import { useAction } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import { tenantsApi, useTenants } from '@/lib/tenants'
import { tenantApprovalsApi } from '@/lib/tenant-approvals'
import { navigate } from '@/lib/router'
import { VERTICAL_CATALOG, getVertical } from '@/lib/verticals'
import type { FeatureFlag, ResolvedProfile, Tenant } from '@/lib/types'
import {
  Card,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  FieldTextarea,
  GhostButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  TagField,
  Toggle,
} from '@/components/remake/ui'
import { VerticalGlyph } from '@/components/vertical-icon'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

const DAYS = [
  { value: 1, label: 'Mon' },
  { value: 2, label: 'Tue' },
  { value: 3, label: 'Wed' },
  { value: 4, label: 'Thu' },
  { value: 5, label: 'Fri' },
  { value: 6, label: 'Sat' },
  { value: 0, label: 'Sun' },
]

const fieldLabelClass = 'mb-1.5 block text-sm font-medium text-tx'
/** The compact size the legacy `size="sm"` buttons had in this wizard. */
const smallBtn = 'px-3 py-1.5 text-xs'

// The super admin only registers the company basics; an admin completes the
// tenant from WhatsApp onwards, and that completion goes for approval.
const SUPER_STEPS = [{ id: 'company', label: 'Company' }] as const

const ADMIN_STEPS = [
  { id: 'tenant', label: 'Tenant' },
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'brand', label: 'Brand & voice' },
  { id: 'domain', label: 'Your business' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'guardrails', label: 'Guardrails' },
  { id: 'review', label: 'Review' },
] as const

type StepId = (typeof SUPER_STEPS | typeof ADMIN_STEPS)[number]['id']

export function RegisterWizard({ onSubmitted }: { onSubmitted?: () => void } = {}) {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<WizardDraft>(emptyDraft)
  // PDF auto-fill (admin flow only): what the staged document filled in, and
  // a staging failure that must never block manual completion.
  const [pdfSummary, setPdfSummary] = useState<{ filled: string[]; mode: 'kit' | 'universal'; unconfirmed: string[] } | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [pdfBusy, setPdfBusy] = useState(false)
  const { reload, tenants } = useTenants()
  const { identity } = useAuth()
  const action = useAction()
  const toast = useToast()

  const isSuperAdmin = identity?.role === 'super_admin'
  const steps = isSuperAdmin ? SUPER_STEPS : ADMIN_STEPS
  const stepId = steps[step].id as StepId

  const patch = (values: Partial<WizardDraft>) => setDraft((d) => ({ ...d, ...values }))

  // Match registry id/slug only (same rules as backend find_registry_conflict).
  // Do not treat another tenant's display name as "this id is taken".
  const tenantIdTaken = (() => {
    const id = draft.tenantId.trim()
    if (!id) return false
    const norm = (v: string) => v.toLowerCase().replace(/[-_\s]/g, '')
    const n = norm(id)
    return tenants.some((t) => {
      const tid = String(t.id || '')
      const slug = String(t.slug || '')
      return (
        tid === id ||
        slug === id ||
        tid.toLowerCase() === id.toLowerCase() ||
        slug.toLowerCase() === id.toLowerCase() ||
        norm(tid) === n ||
        norm(slug) === n
      )
    })
  })()
  const error = useMemo(
    () => validationError(stepId, draft, tenantIdTaken, Boolean(draft.tenantId.trim())),
    [stepId, draft, tenantIdTaken],
  )

  /** Load the selected tenant's resolved profile into the wizard draft. */
  const pickTenant = async (tenantId: string) => {
    const tenant = tenants.find((t) => t.id === tenantId)
    if (!tenant) return
    setPdfSummary(null)
    setPdfError(null)
    // Prefer the pending draft: it carries every unpublished change (from
    // the profile editor or an earlier wizard pass), so both panels always
    // edit the same working copy.
    const detail = await action.run(() => tenantsApi.detail(tenantId))
    const profile = detail?.pending ?? detail?.effective
    if (profile) {
      setDraft(draftFromProfile(profile as unknown as ResolvedProfile, tenant))
    } else {
      // A tenant the resolver cannot build yet still keeps its basics.
      setDraft({ ...emptyDraft(), companyName: tenant.display_name || tenant.id, tenantId: tenant.id, displayName: tenant.display_name, vertical: tenant.vertical })
    }
  }

  /**
   * Admin-only PDF auto-fill: stage the company's own document against the
   * picked tenant and merge what was extracted into the draft. Fields the
   * admin already typed are never overwritten (see applyStagedToDraft), and
   * a staging failure leaves the wizard fully usable by hand.
   */
  const stageDocument = async (file: File) => {
    const tenantId = draft.tenantId.trim()
    if (!tenantId) return
    setPdfBusy(true)
    setPdfError(null)
    try {
      const staged = await onboardingKitsApi.stageTenant(tenantId, file, 'any')
      const { draft: merged, filled, mode } = applyStagedToDraft(draft, staged)
      setDraft(merged)
      // Universal mode: the template-risky sections the backend could
      // NOT confirm came out of the document (section_sources). The
      // summary names only these as unconfirmed — sections labelled
      // 'extracted' were applied and appear in `filled` instead.
      const src = staged.section_sources ?? {}
      const unconfirmed =
        mode === 'universal'
          ? [
              ...(src.menu === 'extracted' ? [] : ['the menu']),
              ...(src.business_hours === 'extracted' ? [] : ['working hours']),
              ...(src.guardrails === 'extracted' ? [] : ['tone and guardrails']),
            ]
          : []
      setPdfSummary({ filled, mode, unconfirmed })
      if (staged.warning) toast.push(staged.warning, 'error')
      toast.push(
        mode === 'kit'
          ? filled.length
            ? `Fetched from your document: ${filled.join(', ')}`
            : 'Document read — every field it covers was already filled in.'
          : filled.length
            ? `Pre-filled from your document: ${filled.join(', ')} — please review the other steps`
            : 'Document read — its details could not be confirmed, so nothing was pre-filled.',
      )
    } catch (err) {
      setPdfError(
        err instanceof Error
          ? err.message
          : 'Could not read that document. You can still complete every step by hand.',
      )
    } finally {
      setPdfBusy(false)
    }
  }

  const submit = async () => {
    if (isSuperAdmin) {
      // The super admin registers the company only. No publish: an admin
      // completes the tenant, and that completion needs approval.
      const created = await action.run(() =>
        tenantsApi.create({
          tenant_id: draft.tenantId.trim(),
          slug: draft.tenantId.trim(),
          vertical: draft.vertical,
          display_name: draft.displayName || draft.companyName,
          status: 'active',
          // Registration basics travel with the create call itself: the
          // profile-draft endpoint is tenant-admin territory (super admins
          // are view-only inside tenants), and auto-scrape stays off so
          // registering never publishes website-derived content.
          website: draft.website.trim(),
          auto_scrape: false,
        }),
      )
      if (!created) return

      const id = created.tenant.id
      toast.push(`${draft.companyName} registered. An admin can now complete it from WhatsApp onwards.`)
      reload()
      navigate(`/tenants/${encodeURIComponent(id)}/overview`)
      return
    }

    // Admin: bind the WhatsApp number, save the completed data as the
    // tenant's draft, then queue the publish for super admin approval.
    const id = draft.tenantId.trim()
    if (draft.wabaPhoneId.trim()) {
      const bound = await action.run(() => tenantsApi.bindPhone(id, draft.wabaPhoneId.trim()))
      if (!bound) return
    }
    const saved = await action.run(() => tenantsApi.saveDraft(id, draftToSnapshot(draft)))
    if (!saved) return
    if ((saved.validation ?? []).length) {
      toast.push('Draft has validation warnings — review them before submitting.', 'error')
      return
    }
    const submitted = await action.run(() => tenantApprovalsApi.submitPublish(id))
    if (!submitted) return
    toast.push(`${draft.companyName} submitted — a super admin must approve it before it goes live.`)
    reload()
    if (onSubmitted) onSubmitted()
    else navigate('/tenant-requests')
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Register a tenant"
        description={
          isSuperAdmin
            ? 'Register the company basics — name, id and business field. An admin then completes the tenant from WhatsApp onwards, and their submission goes for your approval before anything goes live.'
            : 'Pick a tenant the super admin registered, then complete it from WhatsApp onwards. Your submission goes to a super admin for approval before anything goes live.'
        }
      />

      <ol className="mb-7 flex flex-wrap gap-1.5">
        {steps.map((s, index) => {
          const state = index === step ? 'current' : index < step ? 'done' : 'todo'
          return (
            <li key={s.id} className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => index < step && setStep(index)}
                disabled={index > step}
                className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition ${
                  state === 'current'
                    ? 'bg-accsoft text-acc ring-1 ring-inset ring-line'
                    : state === 'done'
                      ? 'text-ok hover:text-ok'
                      : 'text-txmuted'
                }`}
              >
                {state === 'done' ? <Check className="h-3 w-3" /> : <span>{index + 1}</span>}
                {s.label}
              </button>
              {index < steps.length - 1 && <span className="text-txfaint">/</span>}
            </li>
          )
        })}
      </ol>

      <Card>
        {stepId === 'tenant' && (
          <TenantStep
            draft={draft}
            tenants={tenants}
            onPick={pickTenant}
            onStageFile={stageDocument}
            pdfBusy={pdfBusy}
            pdfSummary={pdfSummary}
            pdfError={pdfError}
          />
        )}
        {stepId === 'company' && <CompanyStep draft={draft} patch={patch} />}
        {stepId === 'whatsapp' && <WhatsAppStep draft={draft} patch={patch} />}
        {stepId === 'brand' && <BrandStep draft={draft} patch={patch} />}
        {stepId === 'domain' && <DomainStep draft={draft} patch={patch} />}
        {stepId === 'notifications' && <NotificationsStep draft={draft} patch={patch} />}
        {stepId === 'guardrails' && <GuardrailsStep draft={draft} patch={patch} />}
        {stepId === 'review' && (
          <ReviewStep
            draft={draft}
            onEdit={setStep}
            approvalMode={!isSuperAdmin}
            companyEditable={isSuperAdmin}
          />
        )}

        {action.error && (
          <div className="px-5 pb-4">
            <ErrorBanner title="Registration failed" message={action.error} />
          </div>
        )}

        <div className="flex items-center justify-between border-t border-linesoft px-5 py-4">
          <GhostButton
            disabled={step === 0}
            onClick={() => setStep((s) => Math.max(0, s - 1))}
          >
            <ArrowLeft className="h-4 w-4" />
            Back
          </GhostButton>

          {step < steps.length - 1 ? (
            <PrimaryButton
              disabled={Boolean(error)}
              onClick={() => setStep((s) => s + 1)}
            >
              Continue
              <ArrowRight className="h-4 w-4" />
            </PrimaryButton>
          ) : (
            <PrimaryButton
              disabled={action.busy}
              onClick={submit}
            >
              {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
              {isSuperAdmin ? 'Create tenant' : 'Submit for approval'}
            </PrimaryButton>
          )}
        </div>
      </Card>

      {error && step < steps.length - 1 && (
        <p className="mt-3 text-xs text-attink">{error}</p>
      )}
      {isSuperAdmin && tenantIdTaken && stepId === 'company' && (
        <Notice tone="warn" className="mt-4">
          A tenant with this id already exists. Pick another id, or cancel and edit the existing tenant instead.
        </Notice>
      )}
    </div>
  )
}

// ── steps ──────────────────────────────────────────────────────────────────

/**
 * Admin-only first step: pick one of the tenants the super admin registered.
 * The company basics were entered by the super admin and are read-only here.
 */
function TenantStep({
  draft,
  tenants,
  onPick,
  onStageFile,
  pdfBusy,
  pdfSummary,
  pdfError,
}: {
  draft: WizardDraft
  tenants: Tenant[]
  onPick: (tenantId: string) => void
  onStageFile: (file: File) => void
  pdfBusy: boolean
  pdfSummary: { filled: string[]; mode: 'kit' | 'universal'; unconfirmed: string[] } | null
  pdfError: string | null
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  return (
    <StepShell
      title="Which tenant are you completing?"
      description="The super admin registered the company basics. Pick the tenant, then complete it from WhatsApp onwards — your data goes to a super admin for approval."
      icon={<Building2 className="h-4 w-4" />}
    >
      <div className="space-y-2.5">
        {tenants.length === 0 && (
          <p className="text-sm text-txmuted">
            No tenants yet. A super admin registers the company first from this same panel.
          </p>
        )}
        {tenants.map((tenant) => {
          const vertical = getVertical(tenant.vertical)
          const active = draft.tenantId === tenant.id
          return (
            <button
              key={tenant.id}
              type="button"
              onClick={() => onPick(tenant.id)}
              className={`flex w-full items-center gap-3 rounded-lg p-3.5 text-left ring-1 ring-inset transition ${
                active ? 'bg-accsoft ring-acc' : 'bg-paper ring-line hover:bg-accsoft'
              }`}
            >
              <VerticalGlyph
                name={vertical.icon}
                className={`h-5 w-5 shrink-0 ${active ? 'text-acc' : 'text-txmuted'}`}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-tx">
                  {tenant.display_name || tenant.id}
                </span>
                <span className="mt-0.5 block font-mono text-xs text-txfaint">
                  {tenant.id} · {vertical.short}
                </span>
              </span>
              <Pill tone={tenant.current_version ? 'ok' : 'warn'}>
                {tenant.current_version ? `live v${tenant.current_version}` : 'awaiting completion'}
              </Pill>
            </button>
          )
        })}
      </div>

      {draft.tenantId && (
        <div className="rounded-panel border border-dashed border-line bg-accsoft p-4">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.doc,.md"
            className="hidden"
            aria-label="Upload your company document"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) onStageFile(f)
              e.target.value = ''
            }}
          />
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-card text-acc ring-1 ring-inset ring-line">
              <FileText className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-tx">Have a company document?</p>
              <p className="text-xs leading-relaxed text-txmuted">
                Upload your company PDF, Word file or Markdown and the details it confirms —
                brand, contacts and services, plus the menu, hours and guardrails whenever
                the document itself confirms them — pre-fill the next steps. Anything you
                already typed stays untouched, and everything remains editable before you
                submit for approval.
              </p>
            </div>
            <PrimaryButton
              className={smallBtn}
              disabled={pdfBusy}
              onClick={() => fileRef.current?.click()}
            >
              {pdfBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderUp className="h-4 w-4" />}
              Upload document
            </PrimaryButton>
          </div>

          {pdfError && (
            <Notice tone="bad" title="We could not read that document" className="mt-3">
              {pdfError} You can still complete every step by hand.
            </Notice>
          )}
          {pdfSummary && pdfSummary.mode === 'kit' && (
            <Notice tone="success" title="Fetched from your PDF" className="mt-3">
              {pdfSummary.filled.length ? (
                <>
                  Filled in: {pdfSummary.filled.join(' · ')}. Check each step — everything stays
                  editable, and nothing goes live until a super admin approves your submission.
                </>
              ) : (
                'The document was read, but every field it covers was already filled in — nothing was changed.'
              )}
            </Notice>
          )}
          {pdfSummary && pdfSummary.mode === 'universal' && (
            <Notice
              tone="info"
              title="Started from your document — review the pre-filled steps"
              className="mt-3"
            >
              {pdfSummary.filled.length ? (
                <>
                  Filled in from your document: {pdfSummary.filled.join(' · ')}.{' '}
                  {pdfSummary.unconfirmed.length ? (
                    <>
                      Not applied: {pdfSummary.unconfirmed.join(', ')} — the document did not
                      confirm those, so they keep the standard starting values. Please review
                      those steps.
                    </>
                  ) : (
                    <>
                      The document also confirmed the menu, working hours, tone and guardrails —
                      please still review those steps.
                    </>
                  )}{' '}
                  Everything stays editable, and nothing goes live until a super admin approves
                  your submission.
                </>
              ) : (
                'The document was read, but its details could not be confirmed, so nothing was pre-filled — you can fill the steps in by hand.'
              )}
            </Notice>
          )}
        </div>
      )}
    </StepShell>
  )
}

function StepShell({
  title,
  description,
  icon,
  children,
}: {
  title: string
  description: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <>
      <div className="border-b border-linesoft px-5 py-4">
        <h2 className="flex items-center gap-2 text-base font-semibold text-tx">
          <span className="text-txmuted">{icon}</span>
          {title}
        </h2>
        <p className="mt-1 text-sm leading-relaxed text-txmuted">{description}</p>
      </div>
      <div className="space-y-5 px-5 py-5">{children}</div>
    </>
  )
}

function CompanyStep({ draft, patch }: StepProps) {
  return (
    <StepShell
      title="Who is this tenant?"
      description="The company and the business field decide everything that follows: which questions you are asked, which features are offered, and the words the bot uses."
      icon={<Building2 className="h-4 w-4" />}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <FieldInput
          label="Company name"
          value={draft.companyName}
          onChange={(e) => patch({ companyName: e.target.value, tenantId: draft.tenantId || slugify(e.target.value) })}
          placeholder="Leeway Softech"
          required
        />
        <FieldInput
          label="Tenant id"
          value={draft.tenantId}
          onChange={(e) => patch({ tenantId: slugify(e.target.value) })}
          hint="Lowercase, no spaces. This is the id used in the API and the database."
          required
        />
        <FieldInput
          label="Display name"
          value={draft.displayName}
          onChange={(e) => patch({ displayName: e.target.value })}
          placeholder={draft.companyName || 'Shown in menus'}
        />
        <FieldInput
          label="Website"
          value={draft.website}
          onChange={(e) => patch({ website: e.target.value })}
          placeholder="https://example.com"
        />
      </div>

      <div>
        <p className={fieldLabelClass}>Business field</p>
        <p className="mb-2 text-xs text-txfaint">
          This is the single most important answer: it selects the profile defaults, the vocabulary, the menu and the intents.
        </p>
        <div className="grid gap-2.5 sm:grid-cols-2">
          {VERTICAL_CATALOG.map((v) => {
            const active = draft.vertical === v.id
            return (
              <button
                key={v.id}
                type="button"
                onClick={() => patch(applyVertical(draft, v.id))}
                className={`flex gap-3 rounded-lg p-3.5 text-left ring-1 ring-inset transition ${
                  active
                    ? 'bg-accsoft ring-acc'
                    : 'bg-paper ring-line hover:bg-accsoft'
                }`}
              >
                <VerticalGlyph
                  name={v.icon}
                  className={`mt-0.5 h-5 w-5 shrink-0 ${active ? 'text-acc' : 'text-txmuted'}`}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-tx">{v.label}</span>
                  <span className="mt-1 block text-xs leading-relaxed text-txmuted">{v.blurb}</span>
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </StepShell>
  )
}

function WhatsAppStep({ draft, patch }: StepProps) {
  const toggleDay = (day: number) =>
    patch({
      openDays: draft.openDays.includes(day)
        ? draft.openDays.filter((d) => d !== day)
        : [...draft.openDays, day].sort(),
    })

  return (
    <StepShell
      title="WhatsApp and working hours"
      description="The phone number routes inbound messages to this tenant. Working hours decide when the bot offers a callback instead of a live handoff."
      icon={<CalendarClock className="h-4 w-4" />}
    >
      <FieldInput
        label="WhatsApp phone number *"
        value={draft.wabaPhoneId}
        onChange={(e) => patch({ wabaPhoneId: e.target.value })}
        placeholder="+91 8000305305"
        inputMode="tel"
        hint="The WhatsApp number itself (+91 8000305305) or its 15-digit id from the Meta Business account (WABA). Each number belongs to exactly one tenant."
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <FieldSelect
          label="Timezone *"
          value={draft.timezone}
          onChange={(e) => patch({ timezone: e.target.value })}
          hint="Used by out-of-hours and callback logic."
        >
          <option value="">Select…</option>
          {timezoneOptions(draft.timezone).map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </FieldSelect>
        <FieldInput
          label="Opens"
          type="time"
          value={draft.openTime}
          onChange={(e) => patch({ openTime: e.target.value })}
          disabled={draft.alwaysOpen}
        />
        <FieldInput
          label="Closes"
          type="time"
          value={draft.closeTime}
          onChange={(e) => patch({ closeTime: e.target.value })}
          disabled={draft.alwaysOpen}
        />
      </div>

      <Toggle
        checked={draft.alwaysOpen}
        onChange={(alwaysOpen) => patch({ alwaysOpen })}
        label="Always open"
        description="When off, out-of-hours conversations get a callback flow instead of a handoff."
      />

      {!draft.alwaysOpen && (
        <div className="space-y-3">
          <div>
            <p className={fieldLabelClass}>Open on</p>
            <div className="flex flex-wrap gap-1.5">
              {DAYS.map((day) => {
                const on = draft.openDays.includes(day.value)
                return (
                  <button
                    key={day.value}
                    type="button"
                    onClick={() => toggleDay(day.value)}
                    className={`h-9 w-12 rounded-lg text-xs font-medium ring-1 ring-inset transition ${
                      on
                        ? 'bg-acc text-onacc ring-acc'
                        : 'bg-card text-txmuted ring-line hover:text-tx'
                    }`}
                  >
                    {day.label}
                  </button>
                )
              })}
            </div>
          </div>
          <FieldTextarea
            label="Out-of-hours message"
            value={draft.outOfHoursMessage}
            onChange={(e) => patch({ outOfHoursMessage: e.target.value })}
          />
        </div>
      )}
    </StepShell>
  )
}

function BrandStep({ draft, patch }: StepProps) {
  const v = getVertical(draft.vertical)
  return (
    <StepShell
      title="Brand and voice"
      description="How the bot introduces itself and how it talks. These strings are used verbatim in menus and prompts."
      icon={<MessageSquareText className="h-4 w-4" />}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <FieldInput
          label="Bot name *"
          value={draft.botName}
          onChange={(e) => patch({ botName: e.target.value })}
          placeholder="Asha"
        />
        <FieldSelect
          label="Tone"
          value={draft.tone}
          onChange={(e) => patch({ tone: e.target.value })}
          hint={`${v.short} default: ${v.tone}`}
        >
          <option value="friendly and helpful">Friendly and helpful</option>
          <option value="professional and confident">Professional and confident</option>
          <option value="formal and compliant">Formal and compliant</option>
          <option value="warm and helpful">Warm and helpful</option>
          <option value="calm and empathetic">Calm and empathetic</option>
          <option value="concise and direct">Concise and direct</option>
        </FieldSelect>
        <FieldInput
          label="Tagline"
          value={draft.tagline}
          onChange={(e) => patch({ tagline: e.target.value })}
          placeholder="Software that ships"
        />
        <FieldInput
          label="Closing signature"
          value={draft.signature}
          onChange={(e) => patch({ signature: e.target.value })}
          placeholder="— Team Leeway"
        />
        <FieldInput
          label="Support email *"
          type="email"
          value={draft.supportEmail}
          onChange={(e) => patch({ supportEmail: e.target.value })}
          placeholder="support@example.com"
        />
        <FieldInput
          label="Support phone *"
          value={draft.supportPhone}
          onChange={(e) => patch({ supportPhone: e.target.value })}
          placeholder="+91 98765 43210"
          inputMode="tel"
          error={
            draft.supportPhone.trim() && !isValidPhone(draft.supportPhone)
              ? 'Phone must be a 10-digit number (or country code followed by 10 digits, e.g. +91 98765 43210)'
              : undefined
          }
          hint="Country code followed by a 10-digit number, e.g. +91 98765 43210."
        />
      </div>

      <FieldTextarea
        label="Menu greeting"
        value={draft.greeting}
        onChange={(e) => patch({ greeting: e.target.value })}
        placeholder={`Hi! I'm ${draft.botName || 'the assistant'} from ${draft.companyName || 'our team'}. How can I help?`}
        hint="Shown as the WhatsApp menu body. Leave blank to use the field default."
      />

      <Notice tone="info" title={`Vocabulary for ${v.short}`}>
        The bot will call your offerings <strong>{v.nouns.item}</strong> and your enquiries{' '}
        <strong>{v.nouns.lead}</strong>. You can override every one of these later in the profile editor.
      </Notice>
    </StepShell>
  )
}

function DomainStep({ draft, patch }: StepProps) {
  const v = getVertical(draft.vertical)
  const missingRequired = v.questions.filter((q) => q.required && !answerValue(draft, q.key))

  return (
    <StepShell
      title={`About your ${v.short.toLowerCase()} business`}
      description="These answers become the words the bot recognises. The more specific you are, the fewer questions it gets wrong."
      icon={<Sparkles className="h-4 w-4" />}
    >
      <div className="space-y-5">
        {v.questions.map((q) => {
          const value = draft.answers[q.key]
          return (
            <div key={q.key}>
              <p className={fieldLabelClass}>
                {q.label}
                {q.required && <span className="ml-1 text-badink">*</span>}
              </p>
              {q.kind === 'chips' && (
                <TagField
                  value={Array.isArray(value) ? value : []}
                  onChange={(next) => patch({ answers: { ...draft.answers, [q.key]: next } })}
                  suggestions={q.suggestions ?? []}
                />
              )}
              {q.kind === 'text' && (
                <FieldInput
                  value={typeof value === 'string' ? value : ''}
                  onChange={(e) => patch({ answers: { ...draft.answers, [q.key]: e.target.value } })}
                  placeholder={q.placeholder}
                />
              )}
              {q.kind === 'textarea' && (
                <FieldTextarea
                  value={typeof value === 'string' ? value : ''}
                  onChange={(e) => patch({ answers: { ...draft.answers, [q.key]: e.target.value } })}
                  placeholder={q.placeholder}
                />
              )}
              {q.kind === 'select' && (
                <FieldSelect
                  value={typeof value === 'string' ? value : ''}
                  onChange={(e) => patch({ answers: { ...draft.answers, [q.key]: e.target.value } })}
                >
                  <option value="">Select…</option>
                  {(q.options ?? []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </FieldSelect>
              )}
              {q.help && <p className="mt-1.5 text-xs leading-relaxed text-txmuted">{q.help}</p>}
            </div>
          )
        })}
      </div>

      <Panel title="Optional modules">
        <p className="-mt-1 mb-4 text-sm text-txmuted">
          Switch on only what this company will use — anything off stays hidden from their console.
        </p>
        <div className="space-y-4">
          {([
            { flag: 'payments', label: 'Online Payments', desc: 'Generate Razorpay or Stripe checkout links for WhatsApp orders.' },
            { flag: 'orders', label: 'Orders Management', desc: 'Accept and process customer orders through WhatsApp.' },
            { flag: 'cart', label: 'Cart', desc: 'Allow customers to add multiple items to a cart before checkout.' },
            { flag: 'track_order', label: 'Order Tracking', desc: 'Allow customers to look up order status and shipment AWB on WhatsApp.' },
            { flag: 'quote', label: 'Request a Quote', desc: 'Collect custom requirements and generate quote requests.' },
            { flag: 'callback', label: 'Request a Callback', desc: 'Allow users to request a phone callback from sales or support.' },
            { flag: 'book_appointment', label: 'Book Appointments', desc: 'Allow booking appointments and scheduled meetings.' },
            { flag: 'brochure_pdf', label: 'Brochure PDF', desc: 'Offer downloadable brochure or catalog PDFs in chat.' },
            { flag: 'complaints', label: 'Complaints & Support', desc: 'Register customer grievances and track complaint tickets.' },
            { flag: 'campaigns', label: 'Campaigns', desc: 'Broadcast messages to customer segments from the console.' },
            { flag: 'distributors', label: 'Distributors', desc: 'Manage reseller/distributor network and wholesale pricing tiers.' },
            { flag: 'services', label: 'Services Menu', desc: 'Show the Services menu entry and answer questions about what you deliver.' },
            { flag: 'products', label: 'Products Menu', desc: 'Show the Products menu entry and answer questions about what you sell.' },
          ] as const).map(({ flag, label, desc }) => (
            <Toggle
              key={flag}
              checked={draft.features.includes(flag as FeatureFlag)}
              onChange={(on) =>
                patch({
                  features: on
                    ? [...draft.features.filter((f) => f !== flag), flag as FeatureFlag]
                    : draft.features.filter((f) => f !== flag),
                })
              }
              label={label}
              description={desc}
            />
          ))}
        </div>
      </Panel>

      {missingRequired.length > 0 && (
        <Notice tone="warn">
          Answer {missingRequired.map((q) => q.label).join(', ')} before continuing.
        </Notice>
      )}
    </StepShell>
  )
}

function NotificationsStep({ draft, patch }: StepProps) {
  return (
    <StepShell
      title="Where do enquiries go?"
      description="Leads, quotes and handoffs are pushed to these destinations. A tenant with no destination configured will collect leads that nobody reads."
      icon={<Webhook className="h-4 w-4" />}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <FieldInput
          label="Sales / enquiries email *"
          type="email"
          value={draft.salesEmail}
          onChange={(e) => patch({ salesEmail: e.target.value })}
          placeholder="sales@example.com"
        />
        <FieldInput
          label="Support email"
          type="email"
          value={draft.notificationsSupportEmail}
          onChange={(e) => patch({ notificationsSupportEmail: e.target.value })}
          placeholder="support@example.com"
        />
      </div>
    </StepShell>
  )
}

function GuardrailsStep({ draft, patch }: StepProps) {
  return (
    <StepShell
      title="What must the bot never do?"
      description="Guardrails bind the language model. Statements listed here are refused, and the escalation keywords hand the conversation to a person."
      icon={<ShieldAlert className="h-4 w-4" />}
    >
      <FieldTextarea
        label="Never state (one per line)"
        value={draft.neverState}
        onChange={(e) => patch({ neverState: e.target.value })}
        placeholder={'We guarantee delivery in 24 hours\nOur stock is always in sync\nNo hidden charges, ever'}
        hint="The bot must not claim these. Empty means the vertical default applies."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className={fieldLabelClass}>Forbidden terms</p>
          <TagField
            value={draft.forbiddenTerms}
            onChange={(forbiddenTerms) => patch({ forbiddenTerms })}
            placeholder="Terms to refuse"
          />
        </div>
        <div>
          <p className={fieldLabelClass}>Handoff keywords</p>
          <TagField
            value={draft.handoffKeywords}
            onChange={(handoffKeywords) => patch({ handoffKeywords })}
            placeholder="human, agent…"
          />
        </div>
      </div>
      <div>
        <p className={fieldLabelClass}>Escalate immediately on</p>
        <TagField
          value={draft.escalateKeywords}
          onChange={(escalateKeywords) => patch({ escalateKeywords })}
          placeholder="complaint, fraud, urgent…"
        />
      </div>
      <FieldTextarea
        label="Escalation message"
        value={draft.escalationMessage}
        onChange={(e) => patch({ escalationMessage: e.target.value })}
        rows={2}
      />
      <Notice tone="warn" title="Write these carefully">
        The linter flags a profile when a forbidden term leaks back into its own copy. Keep the two lists
        disjoint.
      </Notice>
    </StepShell>
  )
}

function ReviewStep({
  draft,
  onEdit,
  approvalMode,
  companyEditable,
}: {
  draft: WizardDraft
  onEdit: (step: number) => void
  approvalMode: boolean
  companyEditable: boolean
}) {
  const v = getVertical(draft.vertical)
  const answers = Object.entries(draft.answers).filter(([, value]) =>
    Array.isArray(value) ? value.length : Boolean(value),
  )

  // Document import note: in universal mode, speak per section — a
  // section is claimed as applied only when the backend's provenance
  // label says the document itself confirmed it ('extracted').
  const staged = draft.stagedUpload
  let documentImportNote: string | null = null
  if (staged) {
    const offeringsFaqs = `${staged.offerings?.length ?? 0} offerings and ${staged.faqs?.length ?? 0} FAQs`
    if (staged.extraction_mode === 'kit') {
      documentImportNote = `Menu of ${staged.menu_items?.length ?? 0} item(s) from your document goes into this draft; ${offeringsFaqs} were extracted.`
    } else {
      const src = staged.section_sources ?? {}
      const applied: string[] = []
      if (src.menu === 'extracted')
        applied.push(`the menu (${staged.menu_items?.length ?? 0} items)`)
      if (src.business_hours === 'extracted') applied.push('business hours')
      if (src.guardrails === 'extracted') applied.push('tone and guardrails')
      const skipped: string[] = []
      if (src.menu !== 'extracted') skipped.push('the menu')
      if (src.business_hours !== 'extracted') skipped.push('business hours')
      if (src.guardrails !== 'extracted') skipped.push('tone and guardrails')
      documentImportNote =
        `${offeringsFaqs} were extracted from your document.` +
        (applied.length ? ` Applied from the document: ${applied.join(', ')}.` : '') +
        (skipped.length
          ? ` Not applied — not confirmed in the document: ${skipped.join(', ')}.`
          : '')
    }
  }

  const rows: { label: string; value: React.ReactNode; step: number }[] = [
    { label: 'Company', value: draft.companyName || '—', step: 0 },
    { label: 'Tenant id', value: draft.tenantId || '—', step: 0 },
    { label: 'Field', value: v.label, step: 0 },
    { label: 'Website', value: draft.website || '—', step: 0 },
    { label: 'Phone number id', value: draft.wabaPhoneId || 'not bound', step: 1 },
    {
      label: 'Working hours',
      value: draft.alwaysOpen
        ? 'Always open'
        : `${draft.openTime}–${draft.closeTime} · ${draft.timezone} · ${draft.openDays.length} day(s)`,
      step: 1,
    },
    { label: 'Bot name', value: draft.botName || v.label, step: 2 },
    { label: 'Tone', value: draft.tone, step: 2 },
    { label: 'Support', value: [draft.supportEmail, draft.supportPhone].filter(Boolean).join(' · ') || '—', step: 2 },
    { label: 'Calls it', value: v.nouns.item, step: 2 },
    {
      label: 'Business answers',
      value: answers.length ? (
        <ul className="space-y-1">
          {answers.map(([key, value]) => (
            <li key={key} className="text-xs text-tx">
              <span className="text-txfaint">{key.replace(/_/g, ' ')}:</span>{' '}
              {Array.isArray(value) ? value.join(', ') : value}
            </li>
          ))}
        </ul>
      ) : (
        '—'
      ),
      step: 3,
    },
    {
      label: 'Notifications',
      value:
        [draft.salesEmail, draft.notificationsSupportEmail].filter(Boolean).join(' · ') ||
        'none configured',
      step: 4,
    },
    {
      label: 'Never state',
      value: draft.neverState ? draft.neverState.split('\n').filter(Boolean).length + ' rule(s)' : '—',
      step: 5,
    },
    ...(documentImportNote
      ? [
          {
            label: 'Document import',
            value: documentImportNote,
            step: 0,
          },
        ]
      : []),
  ]

  return (
    <StepShell
      title={approvalMode ? 'Review and submit' : 'Review and publish'}
      description={
        approvalMode
          ? 'Submitting sends everything you added to a super admin for approval. The company basics were set by the super admin and stay read-only. Nothing goes live until they accept it.'
          : 'Creating the tenant registers it, saves this as the draft, then publishes version 1. If validation fails, nothing is published and you can fix it in the profile editor.'
      }
      icon={<BadgeCheck className="h-4 w-4" />}
    >
      <div className="divide-y divide-linesoft rounded-panel border border-line">
        {rows.map((row) => (
          <div key={row.label} className="flex items-start gap-4 px-4 py-3">
            <span className="w-40 shrink-0 text-xs text-txfaint">{row.label}</span>
            <span className="min-w-0 flex-1 text-xs text-tx">
              {typeof row.value === 'string' ? row.value : row.value}
            </span>
            {(companyEditable || row.step > 0) && (
              <button
                type="button"
                onClick={() => onEdit(row.step)}
                className="shrink-0 text-xs text-acc transition hover:opacity-80"
              >
                Edit
              </button>
            )}
          </div>
        ))}
      </div>

      <Notice tone="info" title="What approval does">
        On approval the snapshot is merged over the {v.short} defaults, validated, stored as an immutable
        version, and the runtime cache is purged — so the next message is answered by the profile above.
      </Notice>
    </StepShell>
  )
}

// ── helpers ────────────────────────────────────────────────────────────────

interface StepProps {
  draft: WizardDraft
  patch: (values: Partial<WizardDraft>) => void
}

function answerValue(draft: WizardDraft, key: string): string {
  const value = draft.answers[key]
  if (Array.isArray(value)) return value.join(', ')
  return typeof value === 'string' ? value : ''
}

function validationError(
  stepId: StepId,
  draft: WizardDraft,
  idTaken: boolean,
  tenantSelected: boolean,
): string | null {
  switch (stepId) {
    case 'company':
      if (!draft.companyName.trim()) return 'A company name is required.'
      if (!draft.tenantId.trim()) return 'A tenant id is required.'
      if (idTaken) return 'That tenant id is already registered.'
      if (draft.website.trim() && !URL_RE.test(draft.website.trim()))
        return 'The website does not look like a valid URL (e.g. https://example.com).'
      return null
    case 'tenant':
      if (!tenantSelected) return 'Pick the tenant you are completing.'
      return null
    case 'whatsapp': {
      if (!draft.wabaPhoneId.trim()) return 'A WhatsApp phone number (or its id) is required.'
      const phoneDigits = draft.wabaPhoneId.replace(/\D/g, '')
      const isMetaId = /^\d{15,16}$/.test(phoneDigits)
      const isPhoneNumber = isValidPhone(draft.wabaPhoneId) || isValidPhoneInput(draft.wabaPhoneId)
      if (!isMetaId && !isPhoneNumber)
        return 'Enter a valid 10-digit WhatsApp phone number (e.g. +91 98765 43210) or its 15-digit ID from Meta.'
      if (!draft.timezone.trim()) return 'Select a timezone.'
      if (!draft.alwaysOpen && draft.openDays.length === 0) return 'Pick at least one working day.'
      return null
    }
    case 'brand': {
      if (!draft.botName.trim()) return 'Give the bot a name.'
      if (!draft.supportEmail.trim()) return 'A support email is required.'
      if (!EMAIL_RE.test(draft.supportEmail.trim()))
        return 'The support email does not look valid.'
      if (!draft.supportPhone.trim()) return 'A support phone number is required.'
      if (!isValidPhone(draft.supportPhone))
        return 'The support phone must be a country code followed by a 10-digit number, e.g. +91 98765 43210.'
      return null
    }
    case 'domain': {
      const v = getVertical(draft.vertical)
      const missing = v.questions.filter((q) => q.required && !answerValue(draft, q.key))
      if (missing.length) return `Still needed: ${missing.map((q) => q.label).join(', ')}.`
      return null
    }
    case 'notifications': {
      if (!draft.salesEmail.trim()) return 'A sales / enquiries email is required — leads need a destination.'
      if (!EMAIL_RE.test(draft.salesEmail.trim()))
        return 'The sales / enquiries email does not look valid.'
      if (draft.notificationsSupportEmail.trim() && !EMAIL_RE.test(draft.notificationsSupportEmail.trim()))
        return 'The notifications support email does not look valid.'
      if (draft.brochureUrl.trim() && !URL_RE.test(draft.brochureUrl.trim()))
        return 'The brochure URL does not look like a valid URL.'
      for (const channel of draft.channels) {
        if (!channel.to.trim()) continue
        if (channel.type === 'email' && !EMAIL_RE.test(channel.to.trim()))
          return 'A notification channel email destination does not look valid.'
        if (channel.type === 'webhook' && !URL_RE.test(channel.to.trim()))
          return 'A notification channel webhook destination does not look like a valid URL.'
      }
      return null
    }
    case 'guardrails':
      if (draft.forbiddenTerms.some((t) => draft.neverState.toLowerCase().includes(t.toLowerCase())))
        return 'A forbidden term also appears in your "never state" list.'
      return null
    default:
      return null
  }
}
