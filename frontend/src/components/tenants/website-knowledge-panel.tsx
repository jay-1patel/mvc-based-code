import { useEffect, useState } from 'react'
import {
  BookOpen,
  CheckCircle2,
  Download,
  HelpCircle,
  Loader2,
  Package,
  Play,
  XCircle,
} from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { usePermissions } from '@/lib/permissions'
import { tenantsApi } from '@/lib/tenants'
import {
  crawlApi,
  type CrawlExportFormat,
  type CrawlRun,
  type CrawlStagedResponse,
} from '@/lib/crawl'
import { saveBlob } from '@/lib/uploads'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import {
  EmptyState,
  ErrorBanner,
  GhostButton,
  Notice,
  Panel,
  Pill,
  type PillTone,
  PrimaryButton,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

/**
 * Website Knowledge — the crawl half of onboarding.
 *
 * The tenant's PDF owns the menu, brand and business hours; the website
 * crawl owns knowledge. This screen starts a crawl, lists past runs, and
 * lets the admin read everything a run staged — KB articles, FAQs and
 * product knowledge — download it, and approve or reject it. Approval
 * publishes knowledge only (the backend locks the PDF-owned sections).
 */

/**
 * Page-count label for a run. Crawls are unlimited, so a finished run that
 * fetched everything it found reads "95 pages crawled". The "X of Y"
 * framing is kept only when some discovered pages genuinely did not get
 * scraped (fetch errors, robots, non-HTML) — including historic runs that
 * were recorded under the old 50-page cap.
 */
function pagesCrawledLabel(run: CrawlRun): string {
  const scraped = run.pages_scraped ?? 0
  const found = run.pages_found ?? scraped
  if (run.status === 'done' && found > 0 && scraped >= found) {
    return `${scraped} pages crawled`
  }
  return `${scraped} of ${found} pages crawled`
}

function runStatusTone(status: string): PillTone {
  if (status === 'done') return 'ok'
  if (status === 'failed') return 'bad'
  if (status === 'running' || status === 'queued') return 'warn'
  return 'neutral'
}

function stagedStatusMeta(status: string): { label: string; tone: PillTone } {
  if (status === 'published') return { label: 'Published', tone: 'ok' }
  if (status === 'rejected') return { label: 'Rejected', tone: 'bad' }
  return { label: 'Awaiting review', tone: 'warn' }
}

type KnowledgeTab = 'kb' | 'faqs' | 'products'

export function WebsiteKnowledgePanel({ tenantId }: { tenantId: string }) {
  const { isSuperAdmin } = usePermissions()
  const toast = useToast()
  const action = useAction()
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)

  const runsState = useAsync(
    (signal) => crawlApi.listRuns(tenantId, signal),
    [tenantId],
  )
  const profileState = useAsync(
    (signal) => tenantsApi.resolved(tenantId, signal),
    [tenantId],
  )

  const runs = runsState.data?.history ?? []
  const schedule = runsState.data?.schedule ?? null
  const website =
    profileState.data?.brand?.website || schedule?.url || runs[0]?.source_url || ''

  // Preselect the newest run so its staged knowledge is one glance away.
  useEffect(() => {
    if (selectedRunId === null && runs.length > 0) {
      setSelectedRunId(runs[0].id)
    }
  }, [runs, selectedRunId])

  const running = runs.some((r) => r.status === 'queued' || r.status === 'running')

  const startCrawl = async () => {
    if (!website) return
    const result = await action.run(
      () => crawlApi.start(tenantId, website),
      'Crawl started — staged knowledge will appear here when it finishes.',
    )
    if (result) {
      setSelectedRunId(result.run_id)
      runsState.reload()
    }
  }

  return (
    <div>
      <PageHeader
        title="Website Knowledge"
        description="Your PDF sets the menu, brand and hours. Your website fills the bot's knowledge — articles, FAQs and product details. Crawl it, review what was found, then publish."
      />

      <div className="space-y-5">
        <Panel title="Website crawl">
          <p className="-mt-1 mb-3 text-sm text-txmuted">
            A crawl reads your public website and stages the knowledge it finds. Nothing goes live until you approve a run below.
          </p>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-tx">
                Website:{' '}
                {website ? (
                  <span className="font-medium text-tx">{website}</span>
                ) : (
                  <span className="text-txmuted">not set — add it in the tenant profile first</span>
                )}
              </span>
              <PrimaryButton
                onClick={startCrawl}
                disabled={isSuperAdmin || !website || running || action.busy}
              >
                {action.busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                Start crawl
              </PrimaryButton>
              {running && (
                <Pill tone="warn">A crawl is running…</Pill>
              )}
            </div>
            {isSuperAdmin && (
              <p className="text-xs text-txmuted">
                View-only — super admins can review crawls and download the data,
                but only the tenant's admin can start, approve or reject a crawl.
              </p>
            )}
            {schedule && (
              <p className="text-xs text-txmuted">
                Automatic crawl: every {schedule.interval_hours} hours ·{' '}
                {schedule.is_enabled ? 'on' : 'off'}
                {schedule.last_status
                  ? ` · last run ${schedule.last_status}${
                      schedule.last_pages_found != null
                        ? ` (${schedule.last_pages_found} pages found)`
                        : ''
                    }`
                  : ''}
              </p>
            )}
            {action.error && <ErrorBanner message={action.error} />}
            {action.success && <Notice tone="success">{action.success}</Notice>}
          </div>
        </Panel>

        <Panel title="Crawl runs">
          <p className="-mt-1 mb-3 text-sm text-txmuted">
            Pick a run to read the knowledge it staged.
          </p>
          {runsState.loading && <LoadingBlock label="Loading crawls…" />}
          {runsState.error && <ErrorBanner message={runsState.error} />}
          {!runsState.loading && !runsState.error && runs.length === 0 && (
            <EmptyState
              title="No crawls yet"
              description="Start your first crawl above — the knowledge it finds will be staged here for your review."
            />
          )}
          {runs.length > 0 && (
            <ul className="divide-y divide-linesoft">
              {runs.map((run) => (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedRunId(run.id)}
                    className={cn(
                      'flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-2 py-3 text-left transition-colors hover:bg-paper',
                      selectedRunId === run.id && 'bg-accsoft',
                    )}
                  >
                    <span className="text-sm font-medium text-tx">
                      {formatDate(run.created_at)}
                    </span>
                    <Pill tone={runStatusTone(run.status)}>{run.status}</Pill>
                    <span className="text-xs text-txmuted">
                      {pagesCrawledLabel(run)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-txmuted">
                      {run.source_url}
                    </span>
                    {run.trigger && (
                      <span className="text-xs text-txmuted">{run.trigger}</span>
                    )}
                  </button>
                  {run.status === 'failed' && run.error && (
                    <p className="px-2 pb-3 text-xs text-badink">{run.error}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {selectedRunId !== null && (
          <RunDetail
            key={selectedRunId}
            tenantId={tenantId}
            runId={selectedRunId}
            isSuperAdmin={isSuperAdmin}
            onDecided={() => {
              runsState.reload()
              toast.push('Crawl runs refreshed.')
            }}
          />
        )}
      </div>
    </div>
  )
}

function RunDetail({
  tenantId,
  runId,
  isSuperAdmin,
  onDecided,
}: {
  tenantId: string
  runId: number
  isSuperAdmin: boolean
  onDecided: () => void
}) {
  const action = useAction()
  const [tab, setTab] = useState<KnowledgeTab>('kb')
  const [downloading, setDownloading] = useState<CrawlExportFormat | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const stagedState = useAsync(
    (signal) => crawlApi.staged(tenantId, runId, signal),
    [tenantId, runId],
  )
  const staged: CrawlStagedResponse | null = stagedState.data
  const bifurcation = staged?.payload?.bifurcation ?? {}
  const articles = bifurcation.knowledge_articles ?? []
  const faqs = bifurcation.faqs ?? []
  const products = bifurcation.offerings ?? []
  const pages = staged?.payload?.crawl?.pages ?? staged?.summary?.pages ?? []

  const download = async (format: CrawlExportFormat) => {
    setDownloading(format)
    setDownloadError(null)
    try {
      const blob = await crawlApi.exportBlob(tenantId, runId, format)
      saveBlob(blob, `crawl-run-${runId}-knowledge.${format}`)
    } catch (err) {
      setDownloadError((err as Error)?.message ?? 'Download failed')
    } finally {
      setDownloading(null)
    }
  }

  const decide = async (kind: 'approve' | 'reject') => {
    const result = await action.run(
      () =>
        kind === 'approve'
          ? crawlApi.approve(tenantId, runId)
          : crawlApi.reject(tenantId, runId),
      kind === 'approve'
        ? "Published to your bot's knowledge."
        : 'Crawl rejected — nothing was published.',
    )
    if (result) {
      stagedState.reload()
      onDecided()
    }
  }

  const statusMeta = staged ? stagedStatusMeta(staged.status) : null
  const awaitingReview = staged?.status === 'staged'

  return (
    <Panel
      title={`Run #${runId} — staged knowledge`}
      action={
        <div className="flex flex-wrap items-center gap-2">
          {(['json', 'csv', 'pdf'] as CrawlExportFormat[]).map((format) => (
            <GhostButton
              key={format}
              onClick={() => download(format)}
              disabled={!staged || downloading !== null}
            >
              {downloading === format ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {format.toUpperCase()}
            </GhostButton>
          ))}
        </div>
      }
    >
      <p className="-mt-1 mb-3 text-sm text-txmuted">
        Everything this crawl found, before any of it goes live.
      </p>
      <div className="space-y-4">
        {stagedState.loading && <LoadingBlock label="Loading staged knowledge…" />}
        {stagedState.error && (
          <Notice tone="warn" title="No staged knowledge for this run yet">
            {stagedState.error}
          </Notice>
        )}
        {downloadError && <ErrorBanner message={downloadError} />}
        {action.error && <ErrorBanner message={action.error} />}

        {staged && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {statusMeta && <Pill tone={statusMeta.tone}>{statusMeta.label}</Pill>}
              <Pill>{articles.length} KB articles</Pill>
              <Pill>{faqs.length} FAQs</Pill>
              <Pill>{products.length} products</Pill>
              <Pill>{pages.length} pages</Pill>
              {staged.decided_by && (
                <span className="text-xs text-txmuted">
                  decided by {staged.decided_by}
                  {staged.decided_at ? ` · ${formatDate(staged.decided_at)}` : ''}
                </span>
              )}
            </div>

            {staged.status === 'published' && (
              <Notice tone="success" title="Published to your bot's knowledge">
                This crawl's articles, FAQs and product knowledge are live.
              </Notice>
            )}

            <Notice tone="info" title="Your PDF stays in charge">
              Menu, brand and business hours come from your PDF only — website
              data never changes them. Approving this crawl only adds the
              knowledge below.
            </Notice>

            {awaitingReview && !isSuperAdmin && (
              <div className="flex flex-wrap items-center gap-2">
                <PrimaryButton onClick={() => decide('approve')} disabled={action.busy}>
                  <CheckCircle2 className="h-4 w-4" />
                  Approve &amp; publish knowledge
                </PrimaryButton>
                <GhostButton onClick={() => decide('reject')} disabled={action.busy}>
                  <XCircle className="h-4 w-4" />
                  Reject
                </GhostButton>
              </div>
            )}
            {awaitingReview && isSuperAdmin && (
              <p className="text-xs text-txmuted">
                View-only — the tenant's admin approves or rejects crawls.
              </p>
            )}

            <div className="flex gap-1 border-b border-line">
              {(
                [
                  { id: 'kb', label: `Knowledge Base (${articles.length})`, icon: BookOpen },
                  { id: 'faqs', label: `FAQs (${faqs.length})`, icon: HelpCircle },
                  { id: 'products', label: `Product Knowledge (${products.length})`, icon: Package },
                ] as const
              ).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={cn(
                    'flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors',
                    tab === t.id
                      ? 'border-acc text-tx'
                      : 'border-transparent text-txmuted hover:text-tx',
                  )}
                >
                  <t.icon className="h-4 w-4" />
                  {t.label}
                </button>
              ))}
            </div>

            {tab === 'kb' && (
              <div className="space-y-3">
                {articles.length === 0 && (
                  <EmptyState
                    title="No knowledge articles"
                    description="This crawl did not stage any KB articles."
                  />
                )}
                {articles.map((art, i) => (
                  <article
                    key={i}
                    className="rounded-panel border border-line bg-paper p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-sm font-semibold text-tx">
                        {art.title || '(untitled)'}
                      </h4>
                      {art.category && <Pill>{art.category}</Pill>}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-txmuted">
                      {art.markdown_content}
                    </p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'faqs' && (
              <div className="space-y-3">
                {faqs.length === 0 && (
                  <EmptyState
                    title="No FAQs"
                    description="This crawl did not stage any FAQs."
                  />
                )}
                {faqs.map((faq, i) => (
                  <article
                    key={i}
                    className="rounded-panel border border-line bg-paper p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-sm font-semibold text-tx">{faq.question}</h4>
                      {faq.category && <Pill>{faq.category}</Pill>}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-txmuted">
                      {faq.answer}
                    </p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'products' && (
              <div className="space-y-3">
                {products.length === 0 && (
                  <EmptyState
                    title="No product knowledge"
                    description="This crawl did not stage any products or services."
                  />
                )}
                {products.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-line text-xs uppercase tracking-wider text-txmuted">
                          <th className="py-2 pr-3 font-semibold">Name</th>
                          <th className="py-2 pr-3 font-semibold">Category</th>
                          <th className="py-2 pr-3 font-semibold">Price</th>
                          <th className="py-2 font-semibold">Details</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-linesoft">
                        {products.map((prod, i) => (
                          <tr key={i} className="align-top">
                            <td className="py-2 pr-3 font-medium text-tx">{prod.name}</td>
                            <td className="py-2 pr-3 text-txmuted">{prod.category || '—'}</td>
                            <td className="py-2 pr-3 text-txmuted">
                              {prod.price != null && prod.price !== '' ? String(prod.price) : '—'}
                            </td>
                            <td className="py-2 text-txmuted">
                              {prod.description || prod.short_description || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}
