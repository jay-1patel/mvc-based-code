import { useEffect, useState } from 'react'
import {
  BookOpen,
  CheckCircle2,
  Download,
  Globe,
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
  type CrawlStagedResponse,
} from '@/lib/crawl'
import { saveBlob } from '@/lib/uploads'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Alert, EmptyState, LoadingBlock } from '@/components/ui/feedback'
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

type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger'

function runStatusTone(status: string): BadgeTone {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'queued') return 'warning'
  return 'neutral'
}

function stagedStatusMeta(status: string): { label: string; tone: BadgeTone } {
  if (status === 'published') return { label: 'Published', tone: 'success' }
  if (status === 'rejected') return { label: 'Rejected', tone: 'danger' }
  return { label: 'Awaiting review', tone: 'warning' }
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
        <Card>
          <CardHeader
            title="Website crawl"
            description="A crawl reads your public website and stages the knowledge it finds. Nothing goes live until you approve a run below."
            icon={<Globe className="h-4 w-4" />}
          />
          <CardBody className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-slate-300">
                Website:{' '}
                {website ? (
                  <span className="font-medium text-slate-100">{website}</span>
                ) : (
                  <span className="text-slate-500">not set — add it in the tenant profile first</span>
                )}
              </span>
              <Button
                onClick={startCrawl}
                disabled={isSuperAdmin || !website || running || action.busy}
              >
                {action.busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                Start crawl
              </Button>
              {running && (
                <Badge tone="warning">A crawl is running…</Badge>
              )}
            </div>
            {isSuperAdmin && (
              <p className="text-xs text-slate-500">
                View-only — super admins can review crawls and download the data,
                but only the tenant's admin can start, approve or reject a crawl.
              </p>
            )}
            {schedule && (
              <p className="text-xs text-slate-500">
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
            {action.error && <Alert tone="danger">{action.error}</Alert>}
            {action.success && <Alert tone="success">{action.success}</Alert>}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Crawl runs"
            description="Pick a run to read the knowledge it staged."
            icon={<BookOpen className="h-4 w-4" />}
          />
          <CardBody>
            {runsState.loading && <LoadingBlock label="Loading crawls…" />}
            {runsState.error && <Alert tone="danger">{runsState.error}</Alert>}
            {!runsState.loading && !runsState.error && runs.length === 0 && (
              <EmptyState
                title="No crawls yet"
                description="Start your first crawl above — the knowledge it finds will be staged here for your review."
              />
            )}
            {runs.length > 0 && (
              <ul className="divide-y divide-surface-line">
                {runs.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedRunId(run.id)}
                      className={cn(
                        'flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-2 py-3 text-left transition-colors hover:bg-surface-overlay',
                        selectedRunId === run.id && 'bg-surface-overlay',
                      )}
                    >
                      <span className="text-sm font-medium text-slate-100">
                        {formatDate(run.created_at)}
                      </span>
                      <Badge tone={runStatusTone(run.status)}>{run.status}</Badge>
                      <span className="text-xs text-slate-400">
                        {run.pages_scraped ?? 0} of {run.pages_found ?? 0} pages crawled
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
                        {run.source_url}
                      </span>
                      {run.trigger && (
                        <span className="text-xs text-slate-500">{run.trigger}</span>
                      )}
                    </button>
                    {run.status === 'failed' && run.error && (
                      <p className="px-2 pb-3 text-xs text-red-400">{run.error}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

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
    <Card>
      <CardHeader
        title={`Run #${runId} — staged knowledge`}
        description="Everything this crawl found, before any of it goes live."
        icon={<BookOpen className="h-4 w-4" />}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {(['json', 'csv', 'pdf'] as CrawlExportFormat[]).map((format) => (
              <Button
                key={format}
                variant="secondary"
                onClick={() => download(format)}
                disabled={!staged || downloading !== null}
              >
                {downloading === format ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {format.toUpperCase()}
              </Button>
            ))}
          </div>
        }
      />
      <CardBody className="space-y-4">
        {stagedState.loading && <LoadingBlock label="Loading staged knowledge…" />}
        {stagedState.error && (
          <Alert tone="warning" title="No staged knowledge for this run yet">
            {stagedState.error}
          </Alert>
        )}
        {downloadError && <Alert tone="danger">{downloadError}</Alert>}
        {action.error && <Alert tone="danger">{action.error}</Alert>}

        {staged && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {statusMeta && <Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>}
              <Badge tone="neutral">{articles.length} KB articles</Badge>
              <Badge tone="neutral">{faqs.length} FAQs</Badge>
              <Badge tone="neutral">{products.length} products</Badge>
              <Badge tone="neutral">{pages.length} pages</Badge>
              {staged.decided_by && (
                <span className="text-xs text-slate-500">
                  decided by {staged.decided_by}
                  {staged.decided_at ? ` · ${formatDate(staged.decided_at)}` : ''}
                </span>
              )}
            </div>

            {staged.status === 'published' && (
              <Alert tone="success" title="Published to your bot's knowledge">
                This crawl's articles, FAQs and product knowledge are live.
              </Alert>
            )}

            <Alert tone="info" title="Your PDF stays in charge">
              Menu, brand and business hours come from your PDF only — website
              data never changes them. Approving this crawl only adds the
              knowledge below.
            </Alert>

            {awaitingReview && !isSuperAdmin && (
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => decide('approve')} disabled={action.busy}>
                  <CheckCircle2 className="h-4 w-4" />
                  Approve &amp; publish knowledge
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => decide('reject')}
                  disabled={action.busy}
                >
                  <XCircle className="h-4 w-4" />
                  Reject
                </Button>
              </div>
            )}
            {awaitingReview && isSuperAdmin && (
              <p className="text-xs text-slate-500">
                View-only — the tenant's admin approves or rejects crawls.
              </p>
            )}

            <div className="flex gap-1 border-b border-surface-line">
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
                      ? 'border-sky-500 text-slate-100'
                      : 'border-transparent text-slate-400 hover:text-slate-200',
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
                    className="rounded-lg bg-surface-overlay p-4 ring-1 ring-surface-line"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-sm font-semibold text-slate-100">
                        {art.title || '(untitled)'}
                      </h4>
                      {art.category && <Badge tone="neutral">{art.category}</Badge>}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
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
                    className="rounded-lg bg-surface-overlay p-4 ring-1 ring-surface-line"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-sm font-semibold text-slate-100">{faq.question}</h4>
                      {faq.category && <Badge tone="neutral">{faq.category}</Badge>}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
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
                        <tr className="border-b border-surface-line text-xs uppercase tracking-wider text-slate-400">
                          <th className="py-2 pr-3 font-semibold">Name</th>
                          <th className="py-2 pr-3 font-semibold">Category</th>
                          <th className="py-2 pr-3 font-semibold">Price</th>
                          <th className="py-2 font-semibold">Details</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-surface-line">
                        {products.map((prod, i) => (
                          <tr key={i} className="align-top">
                            <td className="py-2 pr-3 font-medium text-slate-100">{prod.name}</td>
                            <td className="py-2 pr-3 text-slate-300">{prod.category || '—'}</td>
                            <td className="py-2 pr-3 text-slate-300">
                              {prod.price != null && prod.price !== '' ? String(prod.price) : '—'}
                            </td>
                            <td className="py-2 text-slate-300">
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
      </CardBody>
    </Card>
  )
}
