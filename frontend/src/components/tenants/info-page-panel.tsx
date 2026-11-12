import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Check, Loader2, Pencil, Plus, Send, X } from 'lucide-react'
import { useAction } from '@/lib/hooks'
import { usePermissions } from '@/lib/permissions'
import { tenantsApi } from '@/lib/tenants'
import type { IntentSpec } from '@/lib/types'
import {
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldTextarea,
  GhostButton,
  IconButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  TagField,
  Toggle,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { useResolved } from '@/components/tenants/hooks'

/**
 * Company info pages for Software & IT tenants — projects, technologies,
 * careers, benefits. Each is one intent with answer text: no flow, no backend
 * service, the text is the whole page. Filled from IT vertical defaults, kit
 * upload ([SECTION: INFO PAGES]), or manual edit. Same save-draft/publish
 * contract as the profile editor. Other verticals do not show these in nav.
 */

export type InfoPageId = 'projects' | 'technologies' | 'careers' | 'benefits'

const PAGES: Record<
  InfoPageId,
  { title: string; description: string; permission: string; subject: string }
> = {
  projects: {
    title: 'Projects',
    description:
      'What the bot answers when a customer asks about your past work, case studies or clients.',
    permission: 'manage_projects',
    subject: 'your past work and case studies',
  },
  technologies: {
    title: 'Technologies',
    description:
      'What the bot answers when a customer asks about your tech stack, tools or languages.',
    permission: 'manage_technologies',
    subject: 'your tech stack',
  },
  careers: {
    title: 'Careers',
    description:
      'What the bot answers when a customer asks about hiring, vacancies or internships.',
    permission: 'manage_careers',
    subject: 'openings and hiring',
  },
  benefits: {
    title: 'Benefits',
    description:
      'What the bot answers when a customer asks why they should work with you.',
    permission: 'manage_benefits',
    subject: 'why teams choose you',
  },
}

interface FormShape {
  answer: string
  keywords: string[]
  enabled: boolean
}

export function InfoPagePanel({ tenantId, page }: { tenantId: string; page: InfoPageId }) {
  const spec = PAGES[page]
  const { data, loading, error, reload } = useResolved(tenantId)
  const { canWriteTenant } = usePermissions()
  const action = useAction()
  const toast = useToast()
  const canEdit = canWriteTenant(spec.permission)

  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<FormShape | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [draftSaved, setDraftSaved] = useState(false)

  const intent = useMemo(() => findIntent(data, page), [data, page])
  const menuButton = useMemo(
    () => (data?.menu.buttons ?? []).find((b) => b.intent === page) ?? null,
    [data, page],
  )
  const isActive = data?.active_intents.some((i) => i.name === page) ?? false

  useEffect(() => {
    if (!intent) return
    setForm(toForm(intent))
    setDraftSaved(false)
  }, [intent])

  if (loading) return <LoadingBlock label={`Loading ${spec.title.toLowerCase()}…`} />
  if (error) {
    return (
      <div>
        <PageHeader title={spec.title} description={spec.description} />
        <ErrorBanner title="Could not load the page" message={error} />
      </div>
    )
  }
  if (!intent || !form) {
    return (
      <div>
        <PageHeader title={spec.title} description={spec.description} />
        <Notice tone="warn" title="No answer configured">
          This tenant's profile has no <code>{page}</code> intent, so the bot never
          answers questions about {spec.subject}. It has to come from the vertical
          defaults or a published profile version first.
        </Notice>
      </div>
    )
  }

  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(intent))

  const save = async (thenPublish: boolean) => {
    const saved = await action.run(() =>
      tenantsApi.saveIntent(tenantId, page, {
        answer: form.answer,
        keywords: form.keywords,
        enabled: form.enabled,
      }),
    )
    if (!saved) return
    setWarnings(saved.validation ?? [])
    setDraftSaved(true)
    if (!thenPublish) {
      toast.push('Draft saved. Nothing is live until you publish.')
      return
    }
    if ((saved.validation ?? []).length) {
      toast.push('Draft has validation warnings — review them before publishing.', 'error')
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
      setDraftSaved(false)
      reload()
    }
  }

  return (
    <div>
      <PageHeader
        title={spec.title}
        description={spec.description}
        meta={
          <>
            <Pill tone={isActive ? 'ok' : 'neutral'}>
              {isActive ? 'the bot answers this' : 'hidden from the bot'}
            </Pill>
            {menuButton ? (
              <Pill tone="ok">
                menu row: “{menuButton.title}” in {menuButton.section}
              </Pill>
            ) : (
              <Pill tone="warn">no menu row points here</Pill>
            )}
          </>
        }
        actions={
          !editing ? (
            canEdit ? (
              <PrimaryButton onClick={() => setEditing(true)}>
                <Pencil className="h-4 w-4" />
                Edit answer
              </PrimaryButton>
            ) : undefined
          ) : (
            <>
              <GhostButton
                onClick={() => {
                  setEditing(false)
                  setForm(toForm(intent))
                  setWarnings([])
                }}
              >
                <X className="h-4 w-4" />
                Cancel
              </GhostButton>
              <GhostButton disabled={!dirty || action.busy} onClick={() => save(false)}>
                {action.busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Check className="h-4 w-4" />
                )}
                Save draft
              </GhostButton>
              <PrimaryButton disabled={!dirty || action.busy} onClick={() => save(true)}>
                {action.busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                Save &amp; publish
              </PrimaryButton>
            </>
          )
        }
      />

      {!canEdit && (
        <Notice tone="warn" title="Read-only" className="mb-4">
          Editing this page needs the <strong>{spec.permission}</strong> permission —
          a super admin can grant it under Team &amp; permissions.
        </Notice>
      )}
      {action.error && (
        <ErrorBanner title="Action failed" message={action.error} className="mb-4" />
      )}
      {warnings.length > 0 && (
        <Notice tone="warn" title="Draft saved with warnings" className="mb-4">
          <ul className="list-disc space-y-1 pl-4">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}
      {draftSaved && !editing && (
        <Notice tone="info" title="Saved to the working draft" className="mb-4">
          The draft carries a newer answer for this page. It goes live when the draft
          is published — from here, or from the Profile or Versions screen.
        </Notice>
      )}

      {editing && form ? (
        <Panel
          title={`Edit the ${spec.title.toLowerCase()} answer`}
          action={
            <Pill tone={form.enabled ? 'ok' : 'neutral'}>
              {form.enabled ? 'will answer' : 'will not answer'}
            </Pill>
          }
        >
          <p className="-mt-1 mb-4 text-sm text-txmuted">
            Saving writes the working draft. Publishing validates it, versions it and swaps the live profile — publishing is the only thing the bot ever sees.
          </p>
          <div className="space-y-5">
            {page === 'careers' ? (
              <CareersEditor
                value={form.answer}
                onChange={(answer) => setForm((f) => (f ? { ...f, answer } : f))}
              />
            ) : page === 'technologies' || page === 'benefits' || page === 'projects' ? (
              <BulletsEditor
                value={form.answer}
                onChange={(answer) => setForm((f) => (f ? { ...f, answer } : f))}
              />
            ) : (
            <FieldTextarea
              label="Answer text"
              rows={8}
              value={form.answer}
              onChange={(e) => setForm((f) => (f ? { ...f, answer: e.target.value } : f))}
              hint='What the bot sends, verbatim. One bullet per line starting with "• "; *stars* make a word bold. Leave empty to stop answering directly — questions then fall through to the knowledge-base pipeline.'
              />
            )}
            <div>
              <TagField
                value={form.keywords}
                onChange={(keywords) => setForm((f) => (f ? { ...f, keywords } : f))}
                placeholder="Add a keyword and press Enter"
              />
              <p className="mt-1 text-xs leading-relaxed text-txmuted">
                Phrases that route a question to this answer. The classifier only ever
                picks intents the tenant has switched on.
              </p>
            </div>
            <Toggle
              checked={form.enabled}
              onChange={(enabled) => setForm((f) => (f ? { ...f, enabled } : f))}
              label="The bot answers questions about this page"
              description="Turn off to keep the text but stop the intent from winning — the menu row disappears too."
            />
            <p className="text-2xs text-txmuted">
              {dirty ? 'Unsaved changes will be written as a draft, not published.' : 'No changes yet.'}
            </p>
          </div>
        </Panel>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Panel title="What the bot replies" className="lg:col-span-2">
            <p className="-mt-1 mb-3 text-sm text-txmuted">
              The live answer for questions about {spec.subject}.
            </p>
            <AnswerPreview text={intent.answer ?? ''} />
          </Panel>
          <Panel title="How customers reach it">
            <p className="-mt-1 mb-3 text-sm text-txmuted">Routing and entry points.</p>
            <div className="space-y-4">
              {intent.keywords?.length ? (
                <div>
                  <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-txmuted">
                    Keywords
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {intent.keywords.map((k) => (
                      <Pill key={k}>{k}</Pill>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-txmuted">No keywords routed to this answer.</p>
              )}
              {intent.examples?.length ? (
                <div>
                  <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-txmuted">
                    Phrases it answers
                  </p>
                  <ul className="space-y-1">
                    {intent.examples.map((ex) => (
                      <li key={ex} className="text-xs italic leading-relaxed text-txmuted">
                        “{ex}”
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div>
                <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-txmuted">
                  Menu row
                </p>
                {menuButton ? (
                  <p className="text-xs leading-relaxed text-tx">
                    “{menuButton.title}” in the {menuButton.section} section of the
                    bot's menu{isActive ? '' : ' — currently hidden because the intent is off'}.
                  </p>
                ) : (
                  <p className="text-xs leading-relaxed text-txmuted">
                    No menu button points here — customers only reach this answer by
                    asking in their own words.
                  </p>
                )}
              </div>
            </div>
          </Panel>
        </div>
      )}
    </div>
  )
}

function findIntent(
  data: ReturnType<typeof useResolved>['data'],
  page: InfoPageId,
): IntentSpec | null {
  if (!data) return null
  return [...data.active_intents, ...data.inactive_intents].find((i) => i.name === page) ?? null
}

function toForm(intent: IntentSpec): FormShape {
  return {
    answer: intent.answer ?? '',
    keywords: [...(intent.keywords ?? [])],
    enabled: intent.enabled ?? true,
  }
}

/** Render the WhatsApp-style answer: "• " bullets, *bold* segments. */
function AnswerPreview({ text }: { text: string }) {
  const lines = text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

  if (!lines.length) {
    return (
      <EmptyState
        title="No answer configured"
        description="Questions about this page fall through to the knowledge-base pipeline instead of a fixed reply."
      />
    )
  }

  return (
    <ul className="space-y-2">
      {lines.map((line, i) => {
        const bullet = line.startsWith('•')
        const body = bullet ? line.slice(1).trim() : line
        return (
          <li key={i} className="flex gap-2.5 text-sm leading-relaxed text-tx">
            {bullet && <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-acc" />}
            <span>{renderInline(body)}</span>
          </li>
        )
      })}
    </ul>
  )
}

function renderInline(text: string): ReactNode[] {
  return text.split(/\*([^*]+)\*/g).map((part, i) =>
    i % 2 === 1 ? (
      <strong key={i} className="font-semibold text-tx">
        {part}
      </strong>
    ) : (
      part
    ),
  )
}

// ── Careers: structured openings editor ─────────────────────────────────────

/** One open role as an editable row. */
interface Opening {
  role: string
  dept: string
  exp: string
  location: string
}

interface CareersShape {
  intro: string
  openings: Opening[]
  closing: string
}

/**
 * The careers page is a table of openings, not prose. The editor keeps one
 * input row per role — role, department, experience and location are all
 * editable, and the department doubles as the bold group heading — and
 * serializes the rows into the WhatsApp-format answer the bot sends.
 * Answers that do not fit the openings format (hand-written or legacy text)
 * fall back to the plain textarea.
 */
function CareersEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [shape, setShape] = useState<CareersShape>(() => parseOpenings(value) ?? { intro: '', openings: [], closing: '' })

  const emit = (next: CareersShape) => {
    setShape(next)
    onChange(serializeOpenings(next))
  }
  const update = (i: number, patch: Partial<Opening>) =>
    emit({ ...shape, openings: shape.openings.map((o, j) => (j === i ? { ...o, ...patch } : o)) })
  const remove = (i: number) => emit({ ...shape, openings: shape.openings.filter((_, j) => j !== i) })
  const add = () =>
    emit({ ...shape, openings: [...shape.openings, { role: '', dept: '', exp: '', location: '' }] })

  return (
    <div className="space-y-4">
      <FieldInput
        label="Heading line"
        value={shape.intro}
        onChange={(e) => emit({ ...shape, intro: e.target.value })}
        hint="The first line of the reply. *Stars* make it bold on WhatsApp."
      />

      <div className="space-y-2">
        {shape.openings.map((o, i) => (
          <div
            key={i}
            className="grid items-end gap-2 rounded-panel border border-line bg-paper p-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
          >
            <FieldInput
              label="Role"
              value={o.role}
              onChange={(e) => update(i, { role: e.target.value })}
              placeholder="Sales Executive"
            />
            <FieldInput
              label="Department"
              value={o.dept}
              onChange={(e) => update(i, { dept: e.target.value })}
              placeholder="Sales"
            />
            <FieldInput
              label="Experience"
              value={o.exp}
              onChange={(e) => update(i, { exp: e.target.value })}
              placeholder="2-4 yrs"
            />
            <FieldInput
              label="Location"
              value={o.location}
              onChange={(e) => update(i, { location: e.target.value })}
              placeholder="Mumbai"
            />
            <IconButton onClick={() => remove(i)} aria-label="Remove role">
              <X className="h-4 w-4" />
            </IconButton>
          </div>
        ))}
        <GhostButton className="px-2.5 py-1.5 text-xs" onClick={add}>
          <Plus className="h-4 w-4" />
          Add role
        </GhostButton>
      </div>

      <FieldInput
        label="Closing line"
        value={shape.closing}
        onChange={(e) => emit({ ...shape, closing: e.target.value })}
        hint="Sent after the openings, e.g. how to apply."
      />

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-txmuted">
          Preview — what the bot sends
        </p>
        <div className="rounded-panel border border-line bg-paper p-3.5">
          <AnswerPreview text={serializeOpenings(shape)} />
        </div>
      </div>
    </div>
  )
}

/** Parse the canonical openings answer; null when the text is not in the format. */
function parseOpenings(text: string): CareersShape | null {
  const lines = text.split('\n').map((s) => s.replace(/\r$/, '').trim()).filter(Boolean)
  const intro: string[] = []
  const closing: string[] = []
  const openings: Opening[] = []
  let dept = ''
  let inOpenings = false
  for (const line of lines) {
    if (line.startsWith('•')) {
      inOpenings = true
      const body = line.slice(1).trim()
      const m = body.match(/^(.+?)\s*[—–-]\s*(.+?)(?:\s*·\s*(.+))?$/)
      if (m) openings.push({ role: m[1], dept, exp: m[2] ?? '', location: m[3] ?? '' })
      else openings.push({ role: body, dept, exp: '', location: '' })
      continue
    }
    const header = line.match(/^\*(.+)\*$/)
    if (header) {
      inOpenings = true
      dept = header[1]
      continue
    }
    if (inOpenings) closing.push(line)
    else intro.push(line)
  }
  if (!openings.length) return null
  return { intro: intro.join('\n'), openings, closing: closing.join('\n') }
}

/** Serialize the rows back into the WhatsApp-format answer. */
function serializeOpenings(shape: CareersShape): string {
  const groups: { dept: string; rows: Opening[] }[] = []
  for (const o of shape.openings) {
    const label = o.dept.trim() || 'Other'
    const existing = groups.find((g) => g.dept === label)
    if (existing) existing.rows.push(o)
    else groups.push({ dept: label, rows: [o] })
  }
  const parts: string[] = []
  if (shape.intro.trim()) parts.push(shape.intro.trim())
  for (const g of groups) {
    parts.push('')
    parts.push(`*${g.dept}*`)
    for (const o of g.rows) {
      const tail = [o.exp.trim(), o.location.trim()].filter(Boolean).join(' · ')
      parts.push(tail ? `• ${o.role.trim() || 'Role'} — ${tail}` : `• ${o.role.trim() || 'Role'}`)
    }
  }
  if (shape.closing.trim()) {
    parts.push('')
    parts.push(shape.closing.trim())
  }
  return parts.join('\n')
}

// ── Technologies / Benefits: labelled bullet editor ──────────────────────────

/** One bold-labelled bullet: a label that is bolded and its detail text. */
interface BulletRow {
  label: string
  details: string
}

interface BulletsShape {
  intro: string
  rows: BulletRow[]
  closing: string
}

/**
 * Technologies and benefits answers are a list of labelled bullets — a bold
 * label and its detail, e.g. "• *Frontend:* modern JavaScript frameworks". The
 * editor gives each bullet its own label and details inputs and writes the
 * "• " and *stars* for you, so a non-technical editor never types them. An
 * answer that is not in this format (hand-written or legacy text) falls back to
 * the plain textarea so nothing is lost.
 */
function BulletsEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [shape, setShape] = useState<BulletsShape>(() => parseBullets(value) ?? { intro: '', rows: [], closing: '' })

  const emit = (next: BulletsShape) => {
    setShape(next)
    onChange(serializeBullets(next))
  }
  const update = (i: number, patch: Partial<BulletRow>) =>
    emit({ ...shape, rows: shape.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
  const remove = (i: number) => emit({ ...shape, rows: shape.rows.filter((_, j) => j !== i) })
  const add = () => emit({ ...shape, rows: [...shape.rows, { label: '', details: '' }] })

  return (
    <div className="space-y-4">
      <FieldInput
        label="Heading line"
        value={shape.intro}
        onChange={(e) => emit({ ...shape, intro: e.target.value })}
        placeholder="Here's the stack we build with:"
        hint="The line before the bullets, sent as its own bullet. Leave empty to start straight with the list."
      />

      <div className="space-y-2">
        {shape.rows.map((r, i) => (
          <div
            key={i}
            className="grid items-end gap-2 rounded-panel border border-line bg-paper p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]"
          >
            <FieldInput
              label="Bold label"
              value={r.label}
              onChange={(e) => update(i, { label: e.target.value })}
              placeholder="Frontend"
            />
            <FieldInput
              label="Details"
              value={r.details}
              onChange={(e) => update(i, { details: e.target.value })}
              placeholder="modern JavaScript frameworks and UI systems"
            />
            <IconButton onClick={() => remove(i)} aria-label="Remove bullet">
              <X className="h-4 w-4" />
            </IconButton>
          </div>
        ))}
        <GhostButton className="px-2.5 py-1.5 text-xs" onClick={add}>
          <Plus className="h-4 w-4" />
          Add bullet
        </GhostButton>
      </div>

      <FieldInput
        label="Closing line"
        value={shape.closing}
        onChange={(e) => emit({ ...shape, closing: e.target.value })}
        placeholder="Tell us what you're building and we'll match the right stack for it."
        hint="Sent after the bullets. Leave empty to finish on the last bullet."
      />

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-txmuted">
          Preview — what the bot sends
        </p>
        <div className="rounded-panel border border-line bg-paper p-3.5">
          <AnswerPreview text={serializeBullets(shape)} />
        </div>
      </div>
    </div>
  )
}

/** Parse a labelled-bullet answer; null when it is not in that format. */
function parseBullets(text: string): BulletsShape | null {
  const lines = text.split('\n').map((s) => s.replace(/\r$/, '').trim()).filter(Boolean)
  const intro: string[] = []
  const closing: string[] = []
  const rows: BulletRow[] = []
  let seenRow = false
  for (const line of lines) {
    const bullet = line.startsWith('•')
    const body = bullet ? line.slice(1).trim() : line
    // A labelled bullet is "• *label* details" — the label is the bold run.
    const m = body.match(/^\*([^*]+)\*\s*(.*)$/)
    if (bullet && m) {
      seenRow = true
      rows.push({ label: m[1].trim(), details: m[2].trim() })
      continue
    }
    // Anything before the first labelled bullet is the heading; anything
    // after it is the closing — both sent as their own bullets.
    if (!seenRow) intro.push(body)
    else closing.push(body)
  }
  if (!rows.length) return null
  return { intro: intro.join('\n'), rows, closing: closing.join('\n') }
}

/** Serialize the labelled rows back into the WhatsApp-format answer. */
function serializeBullets(shape: BulletsShape): string {
  const lines = (block: string) =>
    block.split('\n').map((l) => l.trim()).filter(Boolean)
  const parts: string[] = []
  for (const line of lines(shape.intro)) parts.push(`• ${line}`)
  for (const r of shape.rows) {
    const label = r.label.trim()
    const details = r.details.trim()
    if (!label && !details) continue
    if (label) parts.push(`• *${label}*${details ? ` ${details}` : ''}`)
    else parts.push(`• ${details}`)
  }
  for (const line of lines(shape.closing)) parts.push(`• ${line}`)
  return parts.join('\n')
}
