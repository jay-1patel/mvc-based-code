/**
 * Tenant file uploads — FAQ / knowledge-base / brochure / new-release files.
 *
 * Uploads go up as base64 JSON (the backend already accepts that shape), which
 * keeps the single `api` wrapper as the only transport. Preview and download
 * need the bearer token, so they fetch a blob and hand back an object URL
 * rather than relying on a bare `<a href>` (which would be unauthenticated).
 */

import { api, getToken, ApiError, apiCallStart, logApiCall, logApiError } from './api'

const base = (tenantId: string) => `/api/admin/tenants/${encodeURIComponent(tenantId)}`

export interface BifurcationStats {
  tenant_id?: string
  vertical?: string | null
  brand?: Partial<BifurcationResult['brand']> | Record<string, string>
  menu_buttons_count?: number
  intents_count?: number
  offerings_created?: number
  offerings_sample?: string[]
  faqs_saved?: number
  kb_chunks_indexed?: number
  brand_updated?: boolean
  version?: number
  status?: string
  files_processed?: string[]
  error?: string
}

export interface AdminFile {
  id: number
  name: string
  ext: string | null
  module: string
  size: number
  doc_id: number | null
  file_path: string | null
  url: string | null
  tenant_id: string | null
  created_at: string
  status?: string
  chunks_count?: number
  error_message?: string | null
  bifurcation_stats?: BifurcationStats | string | null
}

export interface KnowledgeGap {
  id: number
  tenant_id: string
  wa_id: string | null
  query: string
  reason: string
  confidence_score: number
  created_at: string
  resolved: number
  resolved_at?: string | null
  resolved_by?: string | null
  resolution_notes?: string | null
}

export interface KnowledgeGapsSummaryQuery {
  query: string
  count: number
  last_asked: string
  avg_score: number
  reason: string
  sample_id: number
}

export interface KnowledgeGapsResponse {
  gaps: KnowledgeGap[]
  summary: {
    total_unresolved: number
    top_queries: KnowledgeGapsSummaryQuery[]
  }
}

export interface OnboardingTemplate {
  vertical: string
  title: string
  icon: string
  docx_url: string | null
  pdf_url: string | null
  has_docx: boolean
  has_pdf: boolean
}

export interface StagedIntent {
  name: string
  description?: string
  keywords?: string[]
  examples?: string[]
  answer?: string
  enabled?: boolean
  flow?: string | null
}

export interface StagedOnboardingDraft {
  status: string
  tenant_id: string
  vertical: string
  brand: {
    name: string
    bot_name: string
    tagline: string
    website: string
    support_email: string
    support_phone: string
    signature?: string
  }
  business_hours: {
    timezone: string
    open: string
    close: string
    open_days: number[]
    raw?: string
  }
  menu_items: Array<{
    id: string
    title: string
    description: string
    section: string
    action: string
  }>
  offerings: Array<{
    name: string
    category: string
    price: string
    short_description: string
    description: string
    unit?: string
    attrs?: Record<string, string>
  }>
  faqs: Array<{
    question: string
    answer: string
    category: string
  }>
  knowledge_articles: Array<{
    title: string
    category: string
    markdown_content: string
  }>
  intents?: StagedIntent[]
  guardrails: {
    tone: string
    never_state: string[]
    escalation_triggers?: string[]
    escalation_rules?: string
    escalation_message?: string
  }
  source_files: string[]
  extraction_mode?: 'kit' | 'universal' | string
  warning?: string
  kit_sections?: Record<string, boolean>
  stats: {
    offerings_count: number
    menu_items_count: number
    faqs_count: number
    articles_count: number
    intents_count?: number
  }
}

export interface BifurcationResult {
  tenant_id: string
  vertical: string
  brand: {
    name: string
    bot_name: string
    tagline: string
    website: string
    support_email: string
    support_phone: string
    signature: string
  }
  menu_buttons_count: number
  intents_count: number
  offerings_created: number
  offerings_sample: string[]
  faqs_saved?: number
  kb_chunks_indexed: number
  brand_updated?: boolean
  version: number
  status: string
  files_processed: string[]
  error?: string
}

/** Parse bifurcation_stats that may arrive as an object or JSON string. */
export function parseBifurcationStats(
  raw: AdminFile['bifurcation_stats'],
): BifurcationStats | null {
  if (!raw) return null
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw) as BifurcationStats
      return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
      return null
    }
  }
  return typeof raw === 'object' ? raw : null
}

/** True when bifurcation_stats explicitly reports an error. */
export function isBifurcationError(stats: BifurcationStats | null | undefined): boolean {
  if (!stats) return false
  return String(stats.status || '').toLowerCase() === 'error'
}

/** Normalize and sum bifurcation_stats from one or more ready admin files. */
export function aggregateBifurcationStats(
  files: AdminFile[],
  filenames?: string[],
): BifurcationResult | null {
  const target = filenames?.length ? new Set(filenames) : null
  const ready = files.filter((f) => {
    if (target && !target.has(f.name)) return false
    if (f.status !== 'ready' || !f.bifurcation_stats) return false
    const stats = parseBifurcationStats(f.bifurcation_stats)
    return Boolean(stats) && !isBifurcationError(stats)
  })
  if (ready.length === 0) return null

  let offerings_created = 0
  let faqs_saved = 0
  let kb_chunks_indexed = 0
  let menu_buttons_count = 0
  let intents_count = 0
  let version = 0
  const offerings_sample: string[] = []
  const files_processed: string[] = []
  let brand: BifurcationResult['brand'] = {
    name: '',
    bot_name: '',
    tagline: '',
    website: '',
    support_email: '',
    support_phone: '',
    signature: '',
  }
  let vertical = 'generic'
  let tenant_id = ''

  for (const file of ready) {
    const stats = parseBifurcationStats(file.bifurcation_stats)
    if (!stats) continue
    offerings_created += Number(stats.offerings_created || 0)
    faqs_saved += Number(stats.faqs_saved || 0)
    kb_chunks_indexed += Number(stats.kb_chunks_indexed || 0)
    menu_buttons_count = Math.max(menu_buttons_count, Number(stats.menu_buttons_count || 0))
    intents_count = Math.max(intents_count, Number(stats.intents_count || 0))
    version = Math.max(version, Number(stats.version || 0))
    for (const name of stats.offerings_sample || []) {
      if (offerings_sample.length < 5 && !offerings_sample.includes(name)) offerings_sample.push(name)
    }
    files_processed.push(file.name)
    if (stats.brand && (stats.brand as { name?: string }).name) {
      brand = { ...brand, ...(stats.brand as BifurcationResult['brand']) }
    }
    if (stats.vertical) vertical = String(stats.vertical)
    if (stats.tenant_id) tenant_id = String(stats.tenant_id)
  }

  return {
    tenant_id,
    vertical,
    brand,
    menu_buttons_count,
    intents_count,
    offerings_created,
    offerings_sample,
    faqs_saved,
    kb_chunks_indexed,
    version,
    status: 'configured',
    files_processed,
  }
}

export type UploadReadyResult = {
  files: AdminFile[]
  timedOut: boolean
}

/** Poll file list until named uploads leave `processing` (or timeout). */
export async function waitForUploadReady(
  tenantId: string,
  filenames: string[],
  opts: { module?: string; timeoutMs?: number; intervalMs?: number } = {},
): Promise<UploadReadyResult> {
  const timeoutMs = opts.timeoutMs ?? 180_000
  const intervalMs = opts.intervalMs ?? 2500
  const wanted = new Set(filenames)
  const started = Date.now()

  while (Date.now() - started < timeoutMs) {
    const files = await uploadsApi.list(tenantId, { module: opts.module })
    const matched = files.filter((f) => wanted.has(f.name))
    if (matched.length >= wanted.size && matched.every((f) => f.status && f.status !== 'processing')) {
      return { files: matched, timedOut: false }
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  const files = await uploadsApi.list(tenantId, { module: opts.module })
  return {
    files: files.filter((f) => wanted.has(f.name)),
    timedOut: true,
  }
}

export const FILE_MODULES = [
  { value: 'faq', label: 'FAQ', permission: 'upload_faq' },
  { value: 'kb', label: 'Knowledge base', permission: 'upload_kb' },
  { value: 'catalogue', label: 'Brochure', permission: 'upload_faq' },
  { value: 'new_arrival', label: 'New releases', permission: 'upload_faq' },
] as const

/**
 * Knowledge-upload limits. Keep in step with `SUPPORTED` / `MAX_UPLOAD_BYTES`
 * in `backend/routes/admin.py`; the picker, the hint text and the server must
 * agree on what is accepted.
 */
export const UPLOAD_ACCEPT = '.pdf,.docx,.doc,.txt,.xlsx,.xls,.md,.csv'
export const UPLOAD_TYPES_LABEL = 'PDF, DOC, DOCX, TXT, XLS, XLSX, MD, CSV'
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
export const MAX_UPLOAD_LABEL = `${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`
export const UPLOAD_EXTENSIONS = new Set(
  UPLOAD_ACCEPT.split(',').map((ext) => ext.trim().toLowerCase()).filter(Boolean),
)

/** Client-side guard before base64 + POST. Throws ApiError-compatible Error. */
export function assertValidUploadFile(file: File, opts: { maxBytes?: number } = {}): void {
  const maxBytes = opts.maxBytes ?? MAX_UPLOAD_BYTES
  if (!file || !file.name) throw new Error('No file selected.')
  if (file.size <= 0) throw new Error(`"${file.name}" is empty.`)
  if (file.size > maxBytes) {
    throw new Error(`"${file.name}" is too large (max ${Math.round(maxBytes / (1024 * 1024))} MB).`)
  }
  const dot = file.name.lastIndexOf('.')
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (!ext || !UPLOAD_EXTENSIONS.has(ext)) {
    throw new Error(`"${file.name}" is not a supported type (${UPLOAD_TYPES_LABEL}).`)
  }
}

export function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the file'))
    reader.onload = () => {
      const result = String(reader.result ?? '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.readAsDataURL(file)
  })
}

export const uploadsApi = {
  list: (tenantId: string, opts: { module?: string; search?: string } = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams()
    if (opts.module) params.set('module', opts.module)
    if (opts.search) params.set('search', opts.search)
    const query = params.toString()
    return api
      .get<{ files: AdminFile[] }>(`${base(tenantId)}/files${query ? `?${query}` : ''}`, signal)
      .then((r) => r.files)
  },

  upload: async (tenantId: string, file: File, module: string) => {
    assertValidUploadFile(file)
    const content = await readAsBase64(file)
    return api.post<{ status: string; filename: string; chunks: number; size: number; file_url: string }>(
      `${base(tenantId)}/files/upload?module=${encodeURIComponent(module)}`,
      { filename: file.name, content, media_type: file.type || undefined },
    )
  },

  remove: (tenantId: string, filename: string) =>
    api.del<{ status: string; deleted_faq: number; deleted_kb: number }>(
      `${base(tenantId)}/files/${encodeURIComponent(filename)}`,
    ),

  autoBifurcate: async (tenantId: string, files: File[], autoPublish = false) => {
    const payload = await Promise.all(
      files.map(async (file) => ({
        filename: file.name,
        content: await readAsBase64(file),
        media_type: file.type || undefined,
      }))
    )
    return api.post<BifurcationResult>(
      `${base(tenantId)}/files/auto-bifurcate?auto_publish=${autoPublish ? 'true' : 'false'}`,
      { files: payload, auto_publish: autoPublish }
    )
  },

  bifurcateExisting: (tenantId: string, filenames?: string[]) =>
    api.post<BifurcationResult>(`${base(tenantId)}/files/bifurcate-existing`, {
      filenames,
      // Stage offerings for review — wrong PDFs must not go live automatically.
      auto_publish: false,
    }),

  /** Fetch the raw file with the bearer token; caller revokes the object URL. */
  blob: async (tenantId: string, filename: string, download = false): Promise<Blob> => {
    const token = getToken()
    const path = `${base(tenantId)}/files/${encodeURIComponent(filename)}/${download ? 'download' : 'preview'}`
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
      logApiError('GET', path, res.status, `Could not fetch the file (${res.status})`, startedAt)
      throw new Error(`Could not fetch the file (${res.status})`)
    }
    logApiCall('GET', path, res.status, startedAt)
    return res.blob()
  },
}

export const knowledgeGapsApi = {
  list: (tenantId: string, resolved = false, limit = 50, signal?: AbortSignal) =>
    api.get<KnowledgeGapsResponse>(
      `${base(tenantId)}/knowledge-gaps?resolved=${resolved ? 'true' : 'false'}&limit=${limit}`,
      signal,
    ),

  resolve: (tenantId: string, gapId: number, notes?: string) =>
    api.post<{ status: string; gap_id: number; resolved: boolean }>(
      `${base(tenantId)}/knowledge-gaps/${gapId}/resolve`,
      { notes },
    ),

  createFaq: (tenantId: string, gapId: number, question: string, answer: string) =>
    api.post<{ status: string; question: string; answer: string; tenant_id: string }>(
      `${base(tenantId)}/knowledge-gaps/${gapId}/create-faq`,
      { question, answer },
    ),
}

export const onboardingKitsApi = {
  list: (signal?: AbortSignal) =>
    api.get<{ templates: OnboardingTemplate[] }>('/api/admin/onboarding-kits/list', signal),

  stageGlobal: async (file: File, lane: 'any' | 'kit' = 'any') => {
    assertValidUploadFile(file)
    const content = await readAsBase64(file)
    return api.post<StagedOnboardingDraft>('/api/admin/onboarding/stage', {
      files: [{ filename: file.name, content }],
      lane,
    })
  },

  stageTenant: async (tenantId: string, file: File, lane: 'any' | 'kit' = 'any') => {
    assertValidUploadFile(file)
    const content = await readAsBase64(file)
    return api.post<StagedOnboardingDraft>(
      `/api/admin/tenants/${encodeURIComponent(tenantId)}/onboarding/stage`,
      { files: [{ filename: file.name, content }], lane },
    )
  },

  publishTenant: (tenantId: string, draft: StagedOnboardingDraft) =>
    api.post<{ status: string; summary: any }>(
      `/api/admin/tenants/${encodeURIComponent(tenantId)}/onboarding/publish`,
      { draft },
    ),
}

/**
 * Upload a record image and get its public URL. Multipart, so it bypasses the
 * JSON `api` wrapper — the bearer token is attached by hand. The backend hosts
 * the file externally and returns the URL the record's Image column stores.
 */
export async function uploadRecordImage(tenantId: string, file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const path = `${base(tenantId)}/files/image`
  const startedAt = apiCallStart()
  let res: Response
  try {
    res = await fetch(path, { method: 'POST', headers, body: form })
  } catch {
    const detail = 'Cannot reach the backend to upload the image.'
    logApiError('POST', path, 0, detail, startedAt)
    throw new ApiError(0, detail)
  }
  if (!res.ok) {
    let message = `Upload failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* keep the default */
    }
    logApiError('POST', path, res.status, message, startedAt)
    throw new ApiError(res.status, message)
  }
  logApiCall('POST', path, res.status, startedAt)
  const body = (await res.json()) as { url?: string }
  if (!body.url) throw new ApiError(502, 'The upload returned no URL.')
  return body.url
}

/**
 * Upload a company logo and get its URL. Multipart, so it bypasses the JSON
 * `api` wrapper. The backend stores it locally (served at `/api/logos/...`),
 * so no third-party host or API key is involved.
 */
export async function uploadImage(file: File): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  const body = await api.postForm<{ url?: string }>('/api/admin/upload/logo', form)
  if (!body?.url) throw new ApiError(502, 'The upload returned no URL.')
  return body.url
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export function openBlob(blob: Blob) {
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank', 'noopener')
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exp
  return `${value.toFixed(value >= 10 || exp === 0 ? 0 : 1)} ${units[exp]}`
}
