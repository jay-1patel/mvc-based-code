import { useRef, useState } from 'react'
import {
  FileText,
  FolderUp,
  Download,
  Eye,
  RefreshCw,
  Trash2,
  Upload,
  Sparkles,
  CheckCircle2,
  Package,
  ArrowRight,
  Bot,
  Compass,
  HelpCircle,
  Lightbulb,
  Check,
  Plus,
  Loader2,
  X,
} from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { useAuth } from '@/lib/auth'
import {
  FILE_MODULES,
  UPLOAD_ACCEPT,
  UPLOAD_TYPES_LABEL,
  MAX_UPLOAD_LABEL,
  formatBytes,
  openBlob,
  saveBlob,
  uploadsApi,
  knowledgeGapsApi,
  aggregateBifurcationStats,
  waitForUploadReady,
  isBifurcationError,
  parseBifurcationStats,
  type AdminFile,
  type BifurcationResult,
  type KnowledgeGap,
} from '@/lib/uploads'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Input, Textarea } from '@/components/ui/input'
import { Alert, EmptyState, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { OnboardingKitWidget } from '@/components/onboarding/onboarding-kit-widget'

/**
 * Uploads is the tenant's own knowledge hub: FAQ documents, KB articles,
 * brochure PDFs, and new-release sheets.
 *
 * Every upload auto-bifurcates in the background: products are staged
 * (inactive) for review, FAQs/KB are indexed, and brand/menu merge only fills
 * empty fields. Off-vertical / junk offerings are rejected. "Re-process
 * library" remains for bulk re-runs.
 */
export function UploadsPanel({ tenantId }: { tenantId: string }) {
  const { can } = useAuth()
  const toast = useToast()
  const action = useAction()
  const inputRef = useRef<HTMLInputElement>(null)

  const [module, setModule] = useState<string>(FILE_MODULES[0].value)
  const [search, setSearch] = useState('')
  const [busyName, setBusyName] = useState<string | null>(null)
  const [bifurcating, setBifurcating] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [bifurcationResult, setBifurcationResult] = useState<BifurcationResult | null>(null)

  // Knowledge Gap Analytics State
  const [showResolvedGaps, setShowResolvedGaps] = useState(false)
  const [gapModalItem, setGapModalItem] = useState<KnowledgeGap | null>(null)
  const [faqQuestion, setFaqQuestion] = useState('')
  const [faqAnswer, setFaqAnswer] = useState('')
  const [creatingFaq, setCreatingFaq] = useState(false)
  const [resolvingId, setResolvingId] = useState<number | null>(null)

  const state = useAsync(
    (signal) => uploadsApi.list(tenantId, { module, search: search || undefined }, signal),
    [tenantId, module, search],
  )

  const gapsState = useAsync(
    (signal) => knowledgeGapsApi.list(tenantId, showResolvedGaps, 50, signal),
    [tenantId, showResolvedGaps],
  )

  const activeModule = FILE_MODULES.find((m) => m.value === module) ?? FILE_MODULES[0]
  const canUpload = can(activeModule.permission)
  const canDelete = can('delete_files')
  const files = state.data ?? []

  const pickUpload = () => inputRef.current?.click()

  const finishUploadBatch = async (filenames: string[]) => {
    toast.push(`Extracting & publishing ${filenames.length} document${filenames.length === 1 ? '' : 's'}…`)
    const { files: readyFiles, timedOut } = await waitForUploadReady(tenantId, filenames, { module })
    state.reload()
    const failed = readyFiles.filter((f) => f.status === 'failed')
    const stillProcessing = readyFiles.filter((f) => f.status === 'processing')
    const bifErrors = readyFiles.filter((f) => {
      if (f.status !== 'ready') return false
      return isBifurcationError(parseBifurcationStats(f.bifurcation_stats))
    })
    if (failed.length) {
      toast.push(
        `${failed.length} file(s) failed: ${failed.map((f) => f.name).join(', ')}`,
        'error',
      )
    }
    if (timedOut || stillProcessing.length) {
      toast.push(
        timedOut
          ? `Still processing after timeout: ${stillProcessing.map((f) => f.name).join(', ') || filenames.join(', ')}. Refresh later.`
          : `${stillProcessing.length} file(s) still processing.`,
        'error',
      )
      return
    }
    if (bifErrors.length) {
      toast.push(
        `Indexed, but auto-config failed for: ${bifErrors.map((f) => f.name).join(', ')}`,
        'error',
      )
    }
    const aggregated = aggregateBifurcationStats(readyFiles, filenames)
    if (aggregated) {
      setBifurcationResult(aggregated)
      const bits = [
        aggregated.offerings_created ? `${aggregated.offerings_created} offerings` : null,
        aggregated.faqs_saved ? `${aggregated.faqs_saved} FAQs` : null,
        aggregated.kb_chunks_indexed ? `${aggregated.kb_chunks_indexed} KB chunks` : null,
      ].filter(Boolean)
      toast.push(
        bits.length
          ? `Published live: ${bits.join(', ')}`
          : 'Upload complete — content indexed (no new catalog items detected).',
      )
    } else if (!failed.length && !bifErrors.length) {
      toast.push('Upload complete — content indexed.')
    }
  }

  const onUploadFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return
    const batch = Array.from(fileList)
    setUploading(true)
    setBifurcationResult(null)
    const uploadedNames: string[] = []
    const failedNames: string[] = []
    try {
      for (const file of batch) {
        try {
          const result = await uploadsApi.upload(tenantId, file, module)
          if (result?.filename) uploadedNames.push(result.filename)
          else {
            failedNames.push(file.name)
            toast.push(`Upload failed: ${file.name}`, 'error')
          }
        } catch (err) {
          failedNames.push(file.name)
          toast.push(`${file.name}: ${(err as Error).message || 'Upload failed'}`, 'error')
        }
      }
      if (uploadedNames.length) {
        await finishUploadBatch(uploadedNames)
      } else if (failedNames.length) {
        state.reload()
      }
    } catch (err) {
      toast.push((err as Error).message || 'Upload failed', 'error')
      state.reload()
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const openFaqCreator = (gap: KnowledgeGap) => {
    setGapModalItem(gap)
    setFaqQuestion(gap.query)
    setFaqAnswer('')
  }

  const onSaveFaq = async () => {
    if (!gapModalItem || !faqAnswer.trim()) {
      toast.push('Please enter an answer for the FAQ.', 'error')
      return
    }
    setCreatingFaq(true)
    try {
      await knowledgeGapsApi.createFaq(tenantId, gapModalItem.id, faqQuestion, faqAnswer)
      toast.push('FAQ published & indexed! Gap resolved.')
      setGapModalItem(null)
      gapsState.reload()
      state.reload()
    } catch (err) {
      toast.push((err as Error).message || 'Failed to create FAQ', 'error')
    } finally {
      setCreatingFaq(false)
    }
  }

  const onResolveGap = async (gap: KnowledgeGap) => {
    setResolvingId(gap.id)
    try {
      await knowledgeGapsApi.resolve(tenantId, gap.id)
      toast.push('Knowledge gap marked resolved.')
      gapsState.reload()
    } catch (err) {
      toast.push((err as Error).message || 'Failed to resolve gap', 'error')
    } finally {
      setResolvingId(null)
    }
  }

  const onBifurcateExisting = async () => {
    if (files.length === 0) {
      toast.push('No uploaded files available to re-process.', 'error')
      return
    }
    setBifurcating(true)
    setBifurcationResult(null)
    const moduleFilenames = files.map((f) => f.name)
    try {
      toast.push(`Re-processing ${moduleFilenames.length} file(s) in this library…`)
      const res = await uploadsApi.bifurcateExisting(tenantId, moduleFilenames)
      if (String(res?.status || '').toLowerCase() === 'error') {
        toast.push(res?.error || 'Re-process failed', 'error')
      } else {
        setBifurcationResult(res)
        toast.push('Re-process complete — catalog & knowledge updated.')
      }
      state.reload()
    } catch (err) {
      toast.push((err as Error).message || 'Re-process failed', 'error')
    } finally {
      setBifurcating(false)
    }
  }

  const remove = async (item: AdminFile) => {
    if (!window.confirm(`Delete ${item.name}? Its indexed content is removed too.`)) return
    const result = await action.run(() => uploadsApi.remove(tenantId, item.name))
    if (result) {
      toast.push(`${item.name} deleted`)
      state.reload()
    }
  }

  const fetchFile = async (item: AdminFile, download: boolean) => {
    setBusyName(item.name)
    try {
      const blob = await uploadsApi.blob(tenantId, item.name, download)
      if (download) saveBlob(blob, item.name)
      else openBlob(blob)
    } catch (err) {
      toast.push((err as Error).message, 'error')
    } finally {
      setBusyName(null)
    }
  }

  if (!can('view_files')) {
    return (
      <div className="min-w-0">
        <PageHeader title="Uploads" />
        <Alert tone="warning" title="Not permitted">
          This screen needs the <strong>view_files</strong> permission.
        </Alert>
      </div>
    )
  }

  return (
    <div className="min-w-0">
      <PageHeader
        title="Uploads & Auto-Configurator"
        description="Upload documents once — products, FAQs, and knowledge are extracted and published live automatically."
        meta={state.data && <Badge tone="accent">{files.length} files</Badge>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              icon={<RefreshCw className={cn('h-4 w-4', state.loading && 'animate-spin')} />}
              onClick={state.reload}
            >
              Refresh
            </Button>
            {files.length > 0 && (
              <Button
                variant="ghost"
                icon={<Sparkles className="h-4 w-4 text-accent-400" />}
                onClick={() => void onBifurcateExisting()}
                loading={bifurcating}
                title="Re-run full bifurcation on files already in this library"
              >
                Re-process library
              </Button>
            )}
          </div>
        }
      />

      <input
        ref={inputRef}
        type="file"
        accept={UPLOAD_ACCEPT}
        multiple
        className="hidden"
        onChange={(e) => void onUploadFiles(e.target.files)}
      />

      {/* Auto-Bifurcation Result Banner */}
      {bifurcationResult && (
        <Card className="mb-6 border-emerald-500/40 bg-emerald-950/20">
          <CardHeader
            title="Auto-Bifurcation Successful & Published Live"
            icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
            actions={
              <Badge tone="accent">
                Version {bifurcationResult.version || 1} Live
              </Badge>
            }
          />
          <CardBody className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
                <p className="text-2xs text-slate-400">Brand Configured</p>
                <p className="font-semibold text-sm text-slate-100">
                  {bifurcationResult.brand?.name || 'Unchanged'}
                </p>
                <p className="text-2xs text-slate-400">{bifurcationResult.brand?.bot_name}</p>
                <p className="mt-1 text-2xs text-emerald-400">Vertical: {bifurcationResult.vertical}</p>
              </div>

              <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
                <p className="text-2xs text-slate-400">Catalog Offerings</p>
                <p className="font-semibold text-lg text-emerald-400">{bifurcationResult.offerings_created}</p>
                <p className="truncate text-2xs text-slate-300">
                  {bifurcationResult.offerings_sample?.join(', ') || 'Populated into catalog'}
                </p>
              </div>

              <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
                <p className="text-2xs text-slate-400">FAQs Saved</p>
                <p className="font-semibold text-lg text-emerald-400">{bifurcationResult.faqs_saved ?? 0}</p>
                <p className="text-2xs text-slate-300">Live in FAQ index</p>
              </div>

              <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
                <p className="text-2xs text-slate-400">WhatsApp Navigation</p>
                <p className="font-semibold text-lg text-emerald-400">{bifurcationResult.menu_buttons_count} Options</p>
                <p className="text-2xs text-slate-300">Formatted for WhatsApp API</p>
              </div>

              <div className="rounded-md border border-emerald-500/20 bg-surface-base/80 p-3">
                <p className="text-2xs text-slate-400">Vector Search / RAG</p>
                <p className="font-semibold text-lg text-emerald-400">{bifurcationResult.kb_chunks_indexed} Chunks</p>
                <p className="text-2xs text-slate-300">FAISS & BM25 Ready</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-surface-line">
              <span className="text-2xs text-slate-400">Inspect configured sections:</span>
              <a
                href={`#/tenants/${tenantId}/records`}
                className="inline-flex items-center gap-1 rounded px-2.5 py-1 text-2xs font-medium bg-surface-panel hover:bg-surface-line text-slate-200 transition"
              >
                <Package className="h-3 w-3 text-amber-400" />
                View Records / Catalog
              </a>
              <a
                href={`#/tenants/${tenantId}/offerings`}
                className="inline-flex items-center gap-1 rounded px-2.5 py-1 text-2xs font-medium bg-surface-panel hover:bg-surface-line text-slate-200 transition"
              >
                <Package className="h-3 w-3 text-amber-400" />
                View Offerings
              </a>
              <a
                href={`#/tenants/${tenantId}/menu`}
                className="inline-flex items-center gap-1 rounded px-2.5 py-1 text-2xs font-medium bg-surface-panel hover:bg-surface-line text-slate-200 transition"
              >
                <Compass className="h-3 w-3 text-sky-400" />
                View WhatsApp Menu
              </a>
              <a
                href={`#/tenants/${tenantId}/profile`}
                className="inline-flex items-center gap-1 rounded px-2.5 py-1 text-2xs font-medium bg-surface-panel hover:bg-surface-line text-slate-200 transition"
              >
                <Bot className="h-3 w-3 text-emerald-400" />
                View Profile
              </a>
              <a
                href={`#/tenants/${tenantId}/test`}
                className="inline-flex items-center gap-1 rounded px-2.5 py-1 text-2xs font-medium bg-accent-100 hover:bg-accent-200 text-accent-800 transition"
              >
                <ArrowRight className="h-3 w-3" />
                Test in Sandbox
              </a>
            </div>
          </CardBody>
        </Card>
      )}

      {/* Predefined Onboarding Kits & AI Bifurcation */}
      <div className="mb-6">
        <OnboardingKitWidget tenantId={tenantId} kitOnly onPublished={() => state.reload()} />
      </div>

      {/* Traditional Upload & Existing Files Section */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {FILE_MODULES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => setModule(m.value)}
              className={cn(
                'rounded-lg px-3 py-1.5 text-xs font-medium transition',
                module === m.value
                  ? 'bg-accent-100 text-accent-800'
                  : 'text-slate-400 hover:bg-surface-panel hover:text-slate-200',
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter files by name…"
            className="w-full sm:w-56"
          />
          {canUpload && (
            <Button
              variant="secondary"
              size="sm"
              icon={<Upload className="h-3.5 w-3.5" />}
              onClick={pickUpload}
              loading={uploading}
            >
              Upload
            </Button>
          )}
        </div>
      </div>

      <p className="mb-4 text-2xs text-slate-500">
        Supported formats: {UPLOAD_TYPES_LABEL} · Max {MAX_UPLOAD_LABEL} per file
      </p>

      {action.error && (
        <Alert tone="danger" title="Upload failed" className="mb-4">
          {action.error}
        </Alert>
      )}

      {!canUpload && (
        <Alert tone="info" className="mb-4">
          You can view {activeModule.label} files, but uploading them needs the{' '}
          <strong>{activeModule.permission}</strong> permission.
        </Alert>
      )}

      <Card className="min-w-0">
        <CardHeader title={`${activeModule.label} files (${files.length})`} icon={<FolderUp className="h-4 w-4" />} />
        {state.loading && !files.length ? (
          <LoadingBlock label="Loading files…" />
        ) : state.error ? (
          <CardBody>
            <Alert tone="danger" title="Could not load files">
              {state.error}
            </Alert>
          </CardBody>
        ) : files.length === 0 ? (
          <EmptyState
            title={`No ${activeModule.label.toLowerCase()} files`}
            description={
              canUpload
                ? 'Upload PDF or Word documents — products, FAQs, and knowledge are extracted and published automatically.'
                : 'Nothing has been uploaded for this module yet.'
            }
            icon={<FileText className="h-6 w-6" />}
          />
        ) : (
          <ul className="divide-y divide-surface-line">
            {files.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center sm:gap-3"
              >
                <div className="flex min-w-0 items-start gap-3 sm:flex-1 sm:items-center">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-accent-400 sm:mt-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="break-all text-sm font-medium text-slate-100 sm:truncate" title={item.name}>
                        {item.name}
                      </p>
                      {item.status === 'processing' && (
                        <Badge tone="warning" className="flex items-center gap-1 text-2xs animate-pulse">
                          <Loader2 className="h-3 w-3 animate-spin" /> Processing
                        </Badge>
                      )}
                      {item.status === 'ready' && (
                        <Badge tone="success" className="text-2xs">
                          {item.chunks_count ?? 0} chunks
                        </Badge>
                      )}
                      {item.status === 'failed' && (
                        <span title={item.error_message || 'Processing failed'}>
                          <Badge tone="danger" className="text-2xs">
                            Failed
                          </Badge>
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-2xs text-slate-500">
                      {(item.ext || 'file').toUpperCase()} · {formatBytes(item.size)} · {formatDate(item.created_at)}
                      {item.error_message ? ` · Error: ${item.error_message}` : ''}
                    </p>
                    {(() => {
                      let stats = item.bifurcation_stats
                      if (typeof stats === 'string') {
                        try {
                          stats = JSON.parse(stats)
                        } catch {
                          return null
                        }
                      }
                      if (!stats || typeof stats !== 'object') return null
                      const s = stats as {
                        offerings_created?: number
                        faqs_saved?: number
                        kb_chunks_indexed?: number
                        status?: string
                      }
                      if (s.status === 'skipped' || s.status === 'disabled' || s.status === 'no_tenant') return null
                      const parts = [
                        s.offerings_created ? `${s.offerings_created} offerings` : null,
                        s.faqs_saved ? `${s.faqs_saved} FAQs` : null,
                        s.kb_chunks_indexed ? `${s.kb_chunks_indexed} KB` : null,
                      ].filter(Boolean)
                      if (!parts.length && s.status !== 'error') return null
                      return (
                        <p
                          className={
                            s.status === 'error'
                              ? 'mt-0.5 text-2xs text-rose-400'
                              : 'mt-0.5 text-2xs text-emerald-400/90'
                          }
                        >
                          {s.status === 'error'
                            ? 'Auto-bifurcation error — content may be indexed without catalog/menu updates'
                            : `Extracted: ${parts.join(' · ')}`}
                        </p>
                      )
                    })()}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 pl-7 sm:pl-0">
                  <Button
                    size="icon"
                    variant="ghost"
                    title="Preview"
                    loading={busyName === item.name}
                    onClick={() => void fetchFile(item, false)}
                  >
                    <Eye className="h-4 w-4" />
                  </Button>
                  <Button size="icon" variant="ghost" title="Download" onClick={() => void fetchFile(item, true)}>
                    <Download className="h-4 w-4" />
                  </Button>
                  {canDelete && (
                    <Button
                      size="icon"
                      variant="ghost"
                      title="Delete"
                      onClick={() => void remove(item)}
                      className="text-rose-400 hover:text-rose-300"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* ── Knowledge Gap Analytics & 1-Click FAQ Generation ─────────────── */}
      <Card className="mt-8 border-amber-500/30 bg-surface-panel/40">
        <CardHeader
          title="Customer Knowledge Gaps & Unanswered Queries"
          icon={<HelpCircle className="h-5 w-5 text-amber-400" />}
          actions={
            <div className="flex items-center gap-2">
              <Button
                variant={showResolvedGaps ? 'secondary' : 'ghost'}
                size="sm"
                onClick={() => setShowResolvedGaps(!showResolvedGaps)}
              >
                {showResolvedGaps ? 'Showing Resolved' : 'Showing Unresolved'}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<RefreshCw className={cn('h-3.5 w-3.5', gapsState.loading && 'animate-spin')} />}
                onClick={gapsState.reload}
              >
                Refresh
              </Button>
            </div>
          }
        />
        <CardBody className="space-y-4">
          <p className="text-xs text-slate-400">
            Real customer questions that scored below bot confidence thresholds or triggered fallback replies.
            Review the top questions the bot failed to answer and 1-click turn them into live FAQ answers to train your AI.
          </p>

          {/* Top Recurring Questions Summary */}
          {gapsState.data?.summary.top_queries && gapsState.data.summary.top_queries.length > 0 && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-950/10 p-3.5">
              <div className="flex items-center gap-2 mb-2 text-xs font-semibold text-amber-300">
                <Lightbulb className="h-4 w-4" />
                <span>Top High-Frequency Questions Asked by Customers</span>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {gapsState.data.summary.top_queries.slice(0, 6).map((tq, idx) => (
                  <div
                    key={idx}
                    className="flex flex-col justify-between rounded border border-surface-line bg-surface-base p-2.5"
                  >
                    <div>
                      <div className="flex items-center justify-between gap-1 mb-1">
                        <Badge tone="warning" className="text-2xs">
                          Asked {tq.count} {tq.count === 1 ? 'time' : 'times'}
                        </Badge>
                        <span className="text-2xs text-slate-500">{formatDate(tq.last_asked)}</span>
                      </div>
                      <p className="text-xs font-medium text-slate-200 line-clamp-2" title={tq.query}>
                        "{tq.query}"
                      </p>
                    </div>
                    <div className="mt-2 pt-2 border-t border-surface-line/50 flex justify-end">
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<Plus className="h-3 w-3" />}
                        onClick={() => openFaqCreator({
                          id: tq.sample_id,
                          tenant_id: tenantId,
                          wa_id: null,
                          query: tq.query,
                          reason: tq.reason,
                          confidence_score: tq.avg_score,
                          created_at: tq.last_asked,
                          resolved: 0,
                        })}
                      >
                        1-Click FAQ
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Knowledge Gaps List */}
          {gapsState.loading && !gapsState.data ? (
            <LoadingBlock label="Loading knowledge gaps..." />
          ) : gapsState.error ? (
            <Alert tone="danger" title="Could not load knowledge gaps">
              {gapsState.error}
            </Alert>
          ) : !gapsState.data?.gaps.length ? (
            <EmptyState
              title={showResolvedGaps ? 'No resolved queries' : 'No unanswered queries detected'}
              description={
                showResolvedGaps
                  ? 'Resolved questions will appear here.'
                  : 'Great job! The AI bot has successfully answered incoming customer questions with high confidence.'
              }
              icon={<CheckCircle2 className="h-6 w-6 text-emerald-400" />}
            />
          ) : (
            <ul className="divide-y divide-surface-line rounded-lg border border-surface-line bg-surface-base overflow-hidden">
              {gapsState.data.gaps.map((gap) => (
                <li key={gap.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-slate-100">"{gap.query}"</span>
                      {gap.reason === 'no_match' && (
                        <Badge tone="danger" className="text-2xs">No FAQ Match</Badge>
                      )}
                      {gap.reason === 'low_confidence' && (
                        <Badge tone="warning" className="text-2xs">
                          Low Confidence ({gap.confidence_score.toFixed(2)})
                        </Badge>
                      )}
                      {gap.reason === 'uncertain_fallback' && (
                        <Badge tone="accent" className="text-2xs">Uncertain Fallback</Badge>
                      )}
                      {gap.resolved === 1 && (
                        <Badge tone="success" className="text-2xs">Resolved</Badge>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-2xs text-slate-500">
                      <span>Asked: {formatDate(gap.created_at)}</span>
                      {gap.wa_id && <span>User: {gap.wa_id}</span>}
                      {gap.resolution_notes && (
                        <span className="text-slate-400 italic">({gap.resolution_notes})</span>
                      )}
                    </div>
                  </div>
                  {gap.resolved === 0 && (
                    <div className="flex shrink-0 items-center gap-2">
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<Plus className="h-3.5 w-3.5" />}
                        onClick={() => openFaqCreator(gap)}
                      >
                        1-Click FAQ
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Check className="h-3.5 w-3.5" />}
                        loading={resolvingId === gap.id}
                        onClick={() => void onResolveGap(gap)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {/* ── 1-Click FAQ Creation Modal ───────────────────────────────────── */}
      {gapModalItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-[2px] p-4">
          <div className="w-full max-w-lg rounded-xl border border-surface-line bg-surface-panel p-5 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-surface-line">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                <Sparkles className="h-4 w-4 text-accent-400" />
                <span>Turn Question into Knowledge Base FAQ</span>
              </div>
              <button
                type="button"
                onClick={() => setGapModalItem(null)}
                className="text-slate-400 hover:text-slate-200 transition"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4 py-4">
              <div>
                <label className="text-xs font-medium text-slate-300 block mb-1">
                  Customer Question
                </label>
                <Input
                  value={faqQuestion}
                  onChange={(e) => setFaqQuestion(e.target.value)}
                  placeholder="E.g., What is your return policy?"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-slate-300 block mb-1">
                  Official FAQ Answer
                </label>
                <Textarea
                  rows={4}
                  value={faqAnswer}
                  onChange={(e) => setFaqAnswer(e.target.value)}
                  placeholder="Enter the clear, direct answer the bot should provide to customers..."
                />
                <p className="mt-1 text-2xs text-slate-400">
                  This will be added to the tenant FAQ dataset and immediately indexed in FAISS vector search.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-surface-line">
              <Button
                variant="ghost"
                onClick={() => setGapModalItem(null)}
                disabled={creatingFaq}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                icon={<Sparkles className="h-4 w-4" />}
                loading={creatingFaq}
                onClick={() => void onSaveFaq()}
              >
                Save & Index FAQ
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
