/**
 * Website crawl — the knowledge half of tenant onboarding.
 *
 * The tenant's PDF/kit owns the menu, brand and business hours. The
 * website crawl owns knowledge: KB articles, FAQs and product knowledge.
 * These calls drive the Website Knowledge screen: start a crawl, review
 * what it staged (nothing is live yet), download it, then approve or
 * reject. Approval publishes knowledge only — never the menu or brand.
 */
import { api, apiCallStart, getToken, logApiCall, logApiError } from './api'

export interface CrawlRun {
  id: number
  tenant_id: string
  source_url: string
  status: 'queued' | 'running' | 'done' | 'failed' | string
  trigger?: string
  pages_found?: number | null
  pages_scraped?: number | null
  error?: string | null
  created_by?: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface CrawlSchedule {
  tenant_id: string
  url: string
  interval_hours: number
  /** Legacy field, no longer applied — crawls have no page-count limit. */
  max_pages: number | null
  auto_publish: boolean
  is_enabled: boolean
  last_status?: string | null
  last_run_at?: string | null
  last_finished_at?: string | null
  last_pages_found?: number | null
  last_error?: string | null
  next_run_at?: string | null
}

export interface CrawlRunsResponse {
  ok: boolean
  tenant_id: string
  latest: CrawlRun | null
  history: CrawlRun[]
  schedule: CrawlSchedule | null
}

export interface StagedKnowledgeArticle {
  title?: string
  category?: string
  markdown_content?: string
}

export interface StagedFaq {
  question?: string
  answer?: string
  category?: string
}

export interface StagedOffering {
  name?: string
  category?: string
  price?: string | number | null
  short_description?: string
  description?: string
  media_url?: string
  detail_url?: string
  attrs?: Record<string, unknown>
}

export interface StagedCrawlPage {
  url?: string
  title?: string
  meta_description?: string
  word_count?: number | null
}

export interface CrawlStagedResponse {
  ok: boolean
  run_id: number
  tenant_id: string
  status: 'staged' | 'published' | 'rejected' | string
  decided_by?: string | null
  decided_at?: string | null
  summary: {
    brand?: string | null
    vertical?: string | null
    offerings_count: number
    faqs_count: number
    pages_found?: number | null
    pages_scraped?: number | null
    pages?: StagedCrawlPage[]
  }
  payload: {
    bifurcation?: {
      knowledge_articles?: StagedKnowledgeArticle[]
      faqs?: StagedFaq[]
      offerings?: StagedOffering[]
    }
    crawl?: {
      root_url?: string
      domain?: string
      pages_found?: number | null
      pages_scraped?: number | null
      pages?: StagedCrawlPage[]
    }
  }
}

export type CrawlExportFormat = 'json' | 'csv' | 'pdf'

const base = (tenantId: string) =>
  `/api/admin/tenants/${encodeURIComponent(tenantId)}`

export const crawlApi = {
  listRuns: (tenantId: string, signal?: AbortSignal) =>
    api.get<CrawlRunsResponse>(`${base(tenantId)}/crawl-runs`, signal),

  // No max_pages is sent: crawls are unlimited and fetch every page found.
  start: (tenantId: string, url: string) =>
    api.post<{ ok: boolean; status: string; run_id: number; run: CrawlRun }>(
      `${base(tenantId)}/crawl`,
      { url },
    ),

  staged: (tenantId: string, runId: number, signal?: AbortSignal) =>
    api.get<CrawlStagedResponse>(
      `${base(tenantId)}/crawl-runs/${runId}/staged`,
      signal,
    ),

  approve: (tenantId: string, runId: number) =>
    api.post<{ ok: boolean; result: Record<string, unknown> }>(
      `${base(tenantId)}/crawl-runs/${runId}/approve`,
    ),

  reject: (tenantId: string, runId: number) =>
    api.post<{ ok: boolean; result: Record<string, unknown> }>(
      `${base(tenantId)}/crawl-runs/${runId}/reject`,
    ),

  /** Fetch the export file with the bearer token; caller saves the blob. */
  exportBlob: async (
    tenantId: string,
    runId: number,
    format: CrawlExportFormat,
  ): Promise<Blob> => {
    const token = getToken()
    const path = `${base(tenantId)}/crawl-runs/${runId}/export?format=${format}`
    const startedAt = apiCallStart()
    let res: Response
    try {
      res = await fetch(path, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
    } catch (err) {
      logApiError('GET', path, 0, (err as Error)?.message || 'Network error', startedAt)
      throw err
    }
    if (!res.ok) {
      logApiError('GET', path, res.status, `Export failed (${res.status})`, startedAt)
      throw new Error(`Export failed (${res.status})`)
    }
    logApiCall('GET', path, res.status, startedAt)
    return res.blob()
  },
}
