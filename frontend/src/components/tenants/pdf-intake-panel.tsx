import { useRef, useState, type ReactNode } from 'react'
import {
  ArrowRight,
  Building2,
  CheckCircle2,
  Compass,
  FileUp,
  HelpCircle,
  Layers,
  Package,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Upload,
} from 'lucide-react'
import { useAuth } from '@/lib/auth'
import {
  MAX_PDF_BYTES,
  pdfIntakeApi,
  type PdfIntakePreview,
  type PdfIntakeSummary,
} from '@/lib/pdf-intake'
import type { StagedOnboardingDraft } from '@/lib/uploads'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Alert } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { cn } from '@/lib/cn'
import { OnboardingReviewModal } from '@/components/onboarding/onboarding-review-modal'

/** An icon per destination, so the placement list reads at a glance. */
const PLACEMENT_ICONS: Record<string, typeof Building2> = {
  brand: Building2,
  business_hours: Compass,
  products: Package,
  services: Package,
  faqs: HelpCircle,
  articles: Layers,
  menu: Compass,
  guardrails: ShieldAlert,
}

/**
 * PDF intake: one document in, every item in its own place.
 *
 * The flow is deliberately two-step. Uploading only *parses* — it shows exactly
 * what would be written where and nothing is saved. The tenant is touched only
 * after the draft has been reviewed and approved, so a bad parse is visible
 * rather than silently overwriting live branding, menu and catalogue data.
 */
export function PdfIntakePanel({ tenantId }: { tenantId: string }) {
  const { can } = useAuth()
  const toast = useToast()
  const inputRef = useRef<HTMLInputElement>(null)

  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<PdfIntakePreview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [summary, setSummary] = useState<PdfIntakeSummary | null>(null)
  const [dragging, setDragging] = useState(false)

  const canUpload = can('upload_kb')

  const reset = () => {
    setPreview(null)
    setSummary(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const ingest = async (fileList: FileList | File[] | null) => {
    if (!fileList) return
    const files = Array.from(fileList as File[])
    if (files.length === 0) return

    const notPdf = files.filter((f) => !f.name.toLowerCase().endsWith('.pdf'))
    if (notPdf.length) {
      toast.push(`Only PDF files are accepted here: ${notPdf.map((f) => f.name).join(', ')}`, 'error')
      return
    }
    const tooBig = files.find((f) => f.size > MAX_PDF_BYTES)
    if (tooBig) {
      toast.push(`${tooBig.name} is larger than ${MAX_PDF_BYTES / (1024 * 1024)} MB`, 'error')
      return
    }

    setBusy(true)
    setSummary(null)
    try {
      const result = await pdfIntakeApi.preview(tenantId, files)
      setPreview(result)
      toast.push(`Read ${result.files.length} document${result.files.length === 1 ? '' : 's'} — review where each item will land.`)
    } catch (err) {
      toast.push((err as Error).message || 'Could not read the document', 'error')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const publish = async (finalDraft: StagedOnboardingDraft) => {
    setPublishing(true)
    try {
      const res = await pdfIntakeApi.publish(tenantId, finalDraft)
      setSummary(res.summary)
      setReviewing(false)
      setPreview(null)
      toast.push('Published — every item is now in its place.')
    } catch (err) {
      toast.push((err as Error).message || 'Publishing failed', 'error')
    } finally {
      setPublishing(false)
    }
  }

  if (!canUpload) {
    return (
      <div className="min-w-0">
        <PageHeader title="PDF Import" />
        <Alert tone="warning" title="Not permitted">
          This screen needs the <strong>upload_kb</strong> permission.
        </Alert>
      </div>
    )
  }

  const placements = preview?.placements ?? {}
  const counts = preview?.placement ?? {}
  const warnings = preview?.warnings ?? []

  return (
    <div className="min-w-0">
      <PageHeader
        title="PDF Import"
        description="Upload a client PDF holding company details, products, services and policies. Each item is read out and placed where it belongs — nothing is written until you approve."
        actions={
          preview || summary ? (
            <Button variant="ghost" icon={<RefreshCw className="h-4 w-4" />} onClick={reset}>
              Start over
            </Button>
          ) : undefined
        }
      />

      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        multiple
        className="hidden"
        onChange={(e) => void ingest(e.target.files)}
      />

      {/* ── Drop zone ─────────────────────────────────────────────────── */}
      <Card className="mb-6 border-accent-500/30 bg-accent-950/20">
        <CardHeader
          title="Upload a client PDF"
          icon={<Sparkles className="h-4 w-4 text-accent-400" />}
          actions={
            <Button
              variant="primary"
              size="sm"
              icon={<Upload className="h-4 w-4" />}
              loading={busy}
              onClick={() => inputRef.current?.click()}
            >
              Choose PDF
            </Button>
          }
        />
        <CardBody>
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              void ingest(e.dataTransfer.files)
            }}
            onClick={() => inputRef.current?.click()}
            className={cn(
              'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition',
              dragging
                ? 'border-accent-400 bg-accent-950/40'
                : 'border-surface-line bg-surface-base/60 hover:border-accent-500/50',
            )}
          >
            <FileUp className={cn('h-7 w-7', busy ? 'animate-pulse text-accent-400' : 'text-slate-400')} />
            <p className="text-sm font-medium text-slate-200">
              {busy ? 'Reading the document…' : 'Drop a PDF here, or click to choose'}
            </p>
            <p className="text-2xs text-slate-500">
              PDF only · up to {MAX_PDF_BYTES / (1024 * 1024)} MB · several files at once is fine
            </p>
          </div>
        </CardBody>
      </Card>

      {/* ── Published result ──────────────────────────────────────────── */}
      {summary && (
        <Card className="mb-6 border-emerald-500/40 bg-emerald-950/20">
          <CardHeader
            title="Published"
            icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
            actions={<Badge tone="accent">Version {summary.version ?? '—'} live</Badge>}
          />
          <CardBody className="space-y-4">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Products placed" value={summary.products_created} note={summary.products_updated ? `${summary.products_updated} updated` : 'new rows'} />
              <Stat label="Services placed" value={summary.services_created} note="category “Services”" />
              <Stat label="FAQs placed" value={summary.faqs_created} note="FAQ dataset" />
              <Stat label="Knowledge indexed" value={summary.kb_chunks_indexed} note="chunks + embeddings" />
            </div>
            {summary.warnings.length > 0 && (
              <Alert tone="warning" title="Published with notes">
                <ul className="ml-4 list-disc space-y-1">
                  {summary.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </Alert>
            )}
            <div className="flex flex-wrap items-center gap-2 border-t border-surface-line pt-3">
              <span className="text-2xs text-slate-400">Check the result:</span>
              <JumpLink href={`#/tenants/${tenantId}/offerings`} icon={<Package className="h-3 w-3 text-amber-400" />} label="Offerings" />
              <JumpLink href={`#/tenants/${tenantId}/menu`} icon={<Compass className="h-3 w-3 text-sky-400" />} label="Menu" />
              <JumpLink href={`#/tenants/${tenantId}/profile`} icon={<Building2 className="h-3 w-3 text-emerald-400" />} label="Profile" />
              <JumpLink href={`#/tenants/${tenantId}/test`} icon={<ArrowRight className="h-3 w-3" />} label="Test in sandbox" />
            </div>
          </CardBody>
        </Card>
      )}

      {/* ── Placement review ──────────────────────────────────────────── */}
      {preview && !summary && (
        <Card className="mb-6">
          <CardHeader
            title="Where everything will go"
            icon={<Layers className="h-4 w-4 text-accent-400" />}
            actions={
              <Button
                variant="primary"
                icon={<Sparkles className="h-4 w-4" />}
                onClick={() => setReviewing(true)}
              >
                Review &amp; approve
              </Button>
            }
          />
          <CardBody className="space-y-4">
            <p className="text-xs text-slate-400">
              Read from <strong className="text-slate-200">{preview.files.join(', ')}</strong>.
              Approving writes each row below to its destination.
            </p>

            <ul className="divide-y divide-surface-line overflow-hidden rounded-lg border border-surface-line">
              {Object.entries(placements).map(([key, info]) => {
                const Icon = PLACEMENT_ICONS[key] ?? Package
                const count = counts[key] ?? 0
                return (
                  <li key={key} className="flex items-center gap-3 bg-surface-base px-4 py-2.5">
                    <Icon className={cn('h-4 w-4 shrink-0', count ? 'text-accent-400' : 'text-slate-600')} />
                    <div className="min-w-0 flex-1">
                      <p className={cn('text-sm font-medium', count ? 'text-slate-100' : 'text-slate-500')}>
                        {info.label}
                      </p>
                      <p className="text-2xs text-slate-500">{info.destination}</p>
                    </div>
                    <Badge tone={count ? 'success' : 'warning'} className="text-2xs">
                      {count ? `${count} item${count === 1 ? '' : 's'}` : 'nothing found'}
                    </Badge>
                  </li>
                )
              })}
            </ul>

            {!preview.profile_valid && (
              <Alert tone="warning" title="Some values need a look">
                The profile did not fully validate. Anything unusable was dropped and is
                listed below — fix it on the review screen before approving.
              </Alert>
            )}

            {warnings.length > 0 && (
              <Alert tone="info" title="Notes from reading the document">
                <ul className="ml-4 list-disc space-y-1">
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </Alert>
            )}
          </CardBody>
        </Card>
      )}

      {reviewing && preview && (
        <OnboardingReviewModal
          draft={preview.draft}
          publishing={publishing}
          onClose={() => setReviewing(false)}
          onConfirm={publish}
        />
      )}
    </div>
  )
}

function Stat({ label, value, note }: { label: string; value: number; note: string }) {
  return (
    <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
      <p className="text-2xs text-slate-400">{label}</p>
      <p className="text-lg font-semibold text-emerald-400">{value}</p>
      <p className="text-2xs text-slate-400">{note}</p>
    </div>
  )
}

function JumpLink({ href, icon, label }: { href: string; icon: ReactNode; label: string }) {
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1 rounded bg-surface-panel px-2.5 py-1 text-2xs font-medium text-slate-200 transition hover:bg-surface-line"
    >
      {icon}
      {label}
    </a>
  )
}
