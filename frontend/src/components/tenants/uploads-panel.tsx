import { useRef, useState } from 'react'
import {
  FileText,
  Download,
  Eye,
  RefreshCw,
  Trash2,
  Upload,
  Sparkles,
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
import { usePermissions } from '@/lib/permissions'
import { navigate } from '@/lib/router'
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
import {
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldTextarea,
  GhostButton,
  IconButton,
  NeedRow,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { Modal } from '@/components/ui/modal'
import { useConfirm } from '@/components/ui/confirm'
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
  const { canWriteTenant, isSuperAdmin } = usePermissions()
  const toast = useToast()
  const confirmDialog = useConfirm()
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
  const canUpload = canWriteTenant(activeModule.permission)
  const canDelete = canWriteTenant('delete_files')
  const files = state.data ?? []

  const failedFiles = files.filter((f) => f.status === 'failed')
  const unresolvedGaps = !showResolvedGaps
    ? (gapsState.data?.gaps ?? []).filter((g) => g.resolved === 0)
    : []

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
    // Report the whole batch in ONE completion toast. This used to push a
    // separate toast per outcome, so a single upload could stack three
    // contradictory toasts at once (failed + auto-config + published).
    const problems: string[] = []
    if (failed.length) {
      problems.push(`${failed.length} file(s) failed: ${failed.map((f) => f.name).join(', ')}`)
    }
    if (timedOut || stillProcessing.length) {
      problems.push(
        timedOut
          ? `Still processing after timeout: ${stillProcessing.map((f) => f.name).join(', ') || filenames.join(', ')}. Refresh later.`
          : `${stillProcessing.length} file(s) still processing.`,
      )
      toast.push(problems.join(' '), 'error')
      return
    }
    if (bifErrors.length) {
      problems.push(`Indexed, but auto-config failed for: ${bifErrors.map((f) => f.name).join(', ')}`)
    }
    const aggregated = aggregateBifurcationStats(readyFiles, filenames)
    let summary = ''
    if (aggregated) {
      setBifurcationResult(aggregated)
      const bits = [
        aggregated.offerings_created ? `${aggregated.offerings_created} offerings` : null,
        aggregated.faqs_saved ? `${aggregated.faqs_saved} FAQs` : null,
        aggregated.kb_chunks_indexed ? `${aggregated.kb_chunks_indexed} KB chunks` : null,
      ].filter(Boolean)
      summary = bits.length
        ? `Published live: ${bits.join(', ')}`
        : 'Upload complete — content indexed (no new catalog items detected).'
    } else if (!failed.length && !bifErrors.length) {
      summary = 'Upload complete — content indexed.'
    }
    const message = [summary, ...problems].filter(Boolean).join(' ')
    if (message) toast.push(message, problems.length ? 'error' : 'success')
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
    if (
      !(await confirmDialog({
        message: `Delete ${item.name}? Its indexed content is removed too.`,
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return
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
        <Notice tone="warn" title="Not permitted">
          This screen needs the <strong>view_files</strong> permission.
        </Notice>
      </div>
    )
  }

  return (
    <div className="min-w-0">
      <PageHeader
        title="Uploads & Auto-Configurator"
        description="Upload documents once — products, FAQs, and knowledge are extracted and published live automatically."
        meta={state.data && <Pill tone="ok">{files.length} files</Pill>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <GhostButton onClick={state.reload}>
              <RefreshCw className={cn('h-4 w-4', state.loading && 'animate-spin')} />
              Refresh
            </GhostButton>
            {files.length > 0 && (
              <GhostButton onClick={() => void onBifurcateExisting()} disabled={bifurcating}>
                {bifurcating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4 text-acc" />
                )}
                Re-process library
              </GhostButton>
            )}
          </div>
        }
      />

      {(failedFiles.length > 0 || unresolvedGaps.length > 0) && (
        <Panel title="Needs you" className="mb-6">
          {failedFiles.length > 0 && (
            <NeedRow icon={<X className="h-4 w-4" />}>
              <strong>{failedFiles.length}</strong> uploaded{' '}
              {failedFiles.length === 1 ? 'file' : 'files'} failed to process —{' '}
              {failedFiles.map((f) => f.name).join(', ')}.
            </NeedRow>
          )}
          {unresolvedGaps.length > 0 && (
            <NeedRow icon={<HelpCircle className="h-4 w-4" />}>
              <strong>{unresolvedGaps.length}</strong> customer{' '}
              {unresolvedGaps.length === 1 ? 'question is' : 'questions are'} still
              unanswered — turn them into FAQs in Knowledge gaps below.
            </NeedRow>
          )}
        </Panel>
      )}

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
        <Panel
          className="mb-6 border-ok/40 bg-oksoft"
          title="Auto-Bifurcation Successful & Published Live"
          action={
            <Pill tone="ok">
              Version {bifurcationResult.version || 1} Live
            </Pill>
          }
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div className="rounded-panel border border-line bg-card p-3">
                <p className="text-2xs text-txmuted">Brand Configured</p>
                <p className="text-sm font-semibold text-tx">
                  {bifurcationResult.brand?.name || 'Unchanged'}
                </p>
                <p className="text-2xs text-txmuted">{bifurcationResult.brand?.bot_name}</p>
                <p className="mt-1 text-2xs text-acc">Vertical: {bifurcationResult.vertical}</p>
              </div>

              <div className="rounded-panel border border-line bg-card p-3">
                <p className="text-2xs text-txmuted">Catalog Offerings</p>
                <p className="text-lg font-semibold text-acc">{bifurcationResult.offerings_created}</p>
                <p className="truncate text-2xs text-tx">
                  {bifurcationResult.offerings_sample?.join(', ') || 'Populated into catalog'}
                </p>
              </div>

              <div className="rounded-panel border border-line bg-card p-3">
                <p className="text-2xs text-txmuted">FAQs Saved</p>
                <p className="text-lg font-semibold text-acc">{bifurcationResult.faqs_saved ?? 0}</p>
                <p className="text-2xs text-tx">Live in FAQ index</p>
              </div>

              <div className="rounded-panel border border-line bg-card p-3">
                <p className="text-2xs text-txmuted">WhatsApp Navigation</p>
                <p className="text-lg font-semibold text-acc">{bifurcationResult.menu_buttons_count} Options</p>
                <p className="text-2xs text-tx">Formatted for WhatsApp API</p>
              </div>

              <div className="rounded-panel border border-line bg-card p-3">
                <p className="text-2xs text-txmuted">Vector Search / RAG</p>
                <p className="text-lg font-semibold text-acc">{bifurcationResult.kb_chunks_indexed} Chunks</p>
                <p className="text-2xs text-tx">FAISS & BM25 Ready</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2">
              <span className="text-2xs text-txmuted">Inspect configured sections:</span>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${tenantId}/records`)}
                className="inline-flex items-center gap-1 rounded-lg bg-card px-2.5 py-1 text-2xs font-medium text-tx transition hover:bg-paper"
              >
                <Package className="h-3 w-3 text-attink" />
                View Records / Catalog
              </button>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${tenantId}/offerings`)}
                className="inline-flex items-center gap-1 rounded-lg bg-card px-2.5 py-1 text-2xs font-medium text-tx transition hover:bg-paper"
              >
                <Package className="h-3 w-3 text-attink" />
                View Offerings
              </button>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${tenantId}/menu`)}
                className="inline-flex items-center gap-1 rounded-lg bg-card px-2.5 py-1 text-2xs font-medium text-tx transition hover:bg-paper"
              >
                <Compass className="h-3 w-3 text-acc" />
                View WhatsApp Menu
              </button>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${tenantId}/profile`)}
                className="inline-flex items-center gap-1 rounded-lg bg-card px-2.5 py-1 text-2xs font-medium text-tx transition hover:bg-paper"
              >
                <Bot className="h-3 w-3 text-acc" />
                View Profile
              </button>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${tenantId}/test`)}
                className="inline-flex items-center gap-1 rounded-lg bg-accsoft px-2.5 py-1 text-2xs font-medium text-acc transition hover:bg-acc hover:text-onacc"
              >
                <ArrowRight className="h-3 w-3" />
                Test in Sandbox
              </button>
            </div>
          </div>
        </Panel>
      )}

      {/* Predefined Onboarding Kits & AI Bifurcation — never for a super
          admin: they are view-only inside a tenant, so no kit upload or
          publish-to-live control is offered to them here. */}
      {!isSuperAdmin && (
        <div className="mb-6">
          <OnboardingKitWidget tenantId={tenantId} kitOnly onPublished={() => state.reload()} />
        </div>
      )}

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
                  ? 'bg-accsoft text-acc'
                  : 'text-txmuted hover:bg-paper hover:text-tx',
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter files by name…"
            aria-label="Filter files by name"
            className="w-full rounded-lg border border-line bg-inputbg px-3 py-2 text-sm text-tx outline-none placeholder:text-txfaint focus:border-acc sm:w-56"
          />
          {canUpload && (
            <GhostButton
              className="px-2.5 py-1.5 text-xs"
              onClick={pickUpload}
              disabled={uploading}
            >
              {uploading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Upload
            </GhostButton>
          )}
        </div>
      </div>

      <p className="mb-4 text-2xs text-txmuted">
        Supported formats: {UPLOAD_TYPES_LABEL} · Max {MAX_UPLOAD_LABEL} per file
      </p>

      {action.error && (
        <ErrorBanner title="Upload failed" message={action.error} className="mb-4" />
      )}

      {!canUpload && (
        <Notice tone="info" className="mb-4">
          You can view {activeModule.label} files, but uploading them needs the{' '}
          <strong>{activeModule.permission}</strong> permission.
        </Notice>
      )}

      <Panel title={`${activeModule.label} files (${files.length})`}>
        {state.loading && !files.length ? (
          <LoadingBlock label="Loading files…" />
        ) : state.error ? (
          <ErrorBanner title="Could not load files" message={state.error} />
        ) : files.length === 0 ? (
          <EmptyState
            title={`No ${activeModule.label.toLowerCase()} files`}
            description={
              canUpload
                ? 'Upload PDF or Word documents — products, FAQs, and knowledge are extracted and published automatically.'
                : 'Nothing has been uploaded for this module yet.'
            }
          />
        ) : (
          <ul className="-mx-4 -mb-4 divide-y divide-linesoft sm:-mx-5 sm:-mb-5">
            {files.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:gap-3 sm:px-5"
              >
                <div className="flex min-w-0 items-start gap-3 sm:flex-1 sm:items-center">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-acc sm:mt-0" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="break-all text-sm font-medium text-tx sm:truncate" title={item.name}>
                        {item.name}
                      </p>
                      {item.status === 'processing' && (
                        <Pill tone="warn" className="animate-pulse">
                          <Loader2 className="h-3 w-3 animate-spin" /> Processing
                        </Pill>
                      )}
                      {item.status === 'ready' && (
                        <Pill tone="ok">{item.chunks_count ?? 0} chunks</Pill>
                      )}
                      {item.status === 'failed' && (
                        <span title={item.error_message || 'Processing failed'}>
                          <Pill tone="bad">Failed</Pill>
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-2xs text-txmuted">
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
                              ? 'mt-0.5 text-2xs text-badink'
                              : 'mt-0.5 text-2xs text-acc'
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
                  <IconButton
                    title="Preview"
                    aria-label="Preview"
                    disabled={busyName === item.name}
                    onClick={() => void fetchFile(item, false)}
                  >
                    {busyName === item.name ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </IconButton>
                  <IconButton
                    title="Download"
                    aria-label="Download"
                    onClick={() => void fetchFile(item, true)}
                  >
                    <Download className="h-4 w-4" />
                  </IconButton>
                  {canDelete && (
                    <IconButton
                      title="Delete"
                      aria-label="Delete"
                      className="text-badink hover:bg-badbg hover:text-badink"
                      onClick={() => void remove(item)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ── Knowledge Gap Analytics & 1-Click FAQ Generation ─────────────── */}
      <Panel
        className="mt-8 border-attline bg-attbg"
        title="Customer Knowledge Gaps & Unanswered Queries"
        action={
          <div className="flex items-center gap-2">
            <GhostButton
              className={cn(
                'px-2.5 py-1.5 text-xs',
                showResolvedGaps && 'border-transparent bg-accsoft text-acc hover:text-acc',
              )}
              onClick={() => setShowResolvedGaps(!showResolvedGaps)}
            >
              {showResolvedGaps ? 'Showing Resolved' : 'Showing Unresolved'}
            </GhostButton>
            <GhostButton className="px-2.5 py-1.5 text-xs" onClick={gapsState.reload}>
              <RefreshCw className={cn('h-3.5 w-3.5', gapsState.loading && 'animate-spin')} />
              Refresh
            </GhostButton>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-xs leading-relaxed text-txmuted">
            Real customer questions that scored below bot confidence thresholds or triggered fallback replies.
            Review the top questions the bot failed to answer and 1-click turn them into live FAQ answers to train your AI.
          </p>

          {/* Top Recurring Questions Summary */}
          {gapsState.data?.summary.top_queries && gapsState.data.summary.top_queries.length > 0 && (
            <div className="rounded-panel border border-attline bg-card p-3.5">
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-attink">
                <Lightbulb className="h-4 w-4" />
                <span>Top High-Frequency Questions Asked by Customers</span>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {gapsState.data.summary.top_queries.slice(0, 6).map((tq, idx) => (
                  <div
                    key={idx}
                    className="flex flex-col justify-between rounded-panel border border-line bg-paper p-2.5"
                  >
                    <div>
                      <div className="mb-1 flex items-center justify-between gap-1">
                        <Pill tone="warn">
                          Asked {tq.count} {tq.count === 1 ? 'time' : 'times'}
                        </Pill>
                        <span className="text-2xs text-txmuted">{formatDate(tq.last_asked)}</span>
                      </div>
                      <p className="line-clamp-2 text-xs font-medium text-tx" title={tq.query}>
                        "{tq.query}"
                      </p>
                    </div>
                    <div className="mt-2 flex justify-end border-t border-linesoft pt-2">
                      <PrimaryButton
                        className="px-2.5 py-1.5 text-xs"
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
                        <Plus className="h-3 w-3" />
                        1-Click FAQ
                      </PrimaryButton>
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
            <ErrorBanner title="Could not load knowledge gaps" message={gapsState.error} />
          ) : !gapsState.data?.gaps.length ? (
            <EmptyState
              title={showResolvedGaps ? 'No resolved queries' : 'No unanswered queries detected'}
              description={
                showResolvedGaps
                  ? 'Resolved questions will appear here.'
                  : 'Great job! The AI bot has successfully answered incoming customer questions with high confidence.'
              }
            />
          ) : (
            <ul className="divide-y divide-linesoft overflow-hidden rounded-panel border border-line bg-card">
              {gapsState.data.gaps.map((gap) => (
                <li key={gap.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-tx">"{gap.query}"</span>
                      {gap.reason === 'no_match' && (
                        <Pill tone="bad">No FAQ Match</Pill>
                      )}
                      {gap.reason === 'low_confidence' && (
                        <Pill tone="warn">
                          Low Confidence ({gap.confidence_score.toFixed(2)})
                        </Pill>
                      )}
                      {gap.reason === 'uncertain_fallback' && (
                        <Pill tone="ok">Uncertain Fallback</Pill>
                      )}
                      {gap.resolved === 1 && (
                        <Pill tone="ok">Resolved</Pill>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-2xs text-txmuted">
                      <span>Asked: {formatDate(gap.created_at)}</span>
                      {gap.wa_id && <span>User: {gap.wa_id}</span>}
                      {gap.resolution_notes && (
                        <span className="italic text-txmuted">({gap.resolution_notes})</span>
                      )}
                    </div>
                  </div>
                  {gap.resolved === 0 && (
                    <div className="flex shrink-0 items-center gap-2">
                      <PrimaryButton
                        className="px-2.5 py-1.5 text-xs"
                        onClick={() => openFaqCreator(gap)}
                      >
                        <Plus className="h-3.5 w-3.5" />
                        1-Click FAQ
                      </PrimaryButton>
                      <GhostButton
                        className="px-2.5 py-1.5 text-xs"
                        disabled={resolvingId === gap.id}
                        onClick={() => void onResolveGap(gap)}
                      >
                        {resolvingId === gap.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                        Dismiss
                      </GhostButton>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      {/* ── 1-Click FAQ Creation Modal ───────────────────────────────────── */}
      {gapModalItem && (
        <Modal
          onClose={() => setGapModalItem(null)}
          className="w-full max-w-lg rounded-panel border border-line bg-card p-5 shadow-2xl"
        >
            <div className="flex items-center justify-between border-b border-line pb-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-tx">
                <Sparkles className="h-4 w-4 text-acc" />
                <span>Turn Question into Knowledge Base FAQ</span>
              </div>
              <button
                type="button"
                onClick={() => setGapModalItem(null)}
                aria-label="Close"
                className="text-txmuted transition hover:text-tx"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4 py-4">
              <FieldInput
                label="Customer Question"
                value={faqQuestion}
                onChange={(e) => setFaqQuestion(e.target.value)}
                placeholder="E.g., What is your return policy?"
              />

              <FieldTextarea
                label="Official FAQ Answer"
                rows={4}
                value={faqAnswer}
                onChange={(e) => setFaqAnswer(e.target.value)}
                placeholder="Enter the clear, direct answer the bot should provide to customers..."
                hint="This will be added to the tenant FAQ dataset and immediately indexed in FAISS vector search."
              />
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-line pt-3">
              <GhostButton
                onClick={() => setGapModalItem(null)}
                disabled={creatingFaq}
              >
                Cancel
              </GhostButton>
              <PrimaryButton
                disabled={creatingFaq}
                onClick={() => void onSaveFaq()}
              >
                {creatingFaq ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
                Save &amp; Index FAQ
              </PrimaryButton>
            </div>
        </Modal>
      )}
    </div>
  )
}
