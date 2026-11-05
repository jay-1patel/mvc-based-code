/**
 * Single fetch wrapper for the whole app.
 *
 * The admin JWT is kept in localStorage and attached as a bearer token. Every
 * non-2xx response is normalised into an `ApiError` carrying the server's
 * `detail`, so screens can surface backend validation text (publish failures,
 * permission refusals) instead of a generic "something went wrong".
 */

const TOKEN_KEY = 'tenant-console.token'
const TENANT_KEY = 'tenant-console.tenant'

export class ApiError extends Error {
  status: number
  requestId?: string
  constructor(status: number, message: string, requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
  }
}

/* ------------------------------------------------------------------ */
/* Console tracing — one line per API call, so any error can be       */
/* traced to its exact method + path + status in DevTools. On by      */
/* default; switch off with localStorage DEBUG_API = '0'.             */
/* ------------------------------------------------------------------ */

/** True unless the user opted out via localStorage DEBUG_API='0'. Never throws. */
export function apiLoggingEnabled(): boolean {
  try {
    return localStorage.getItem('DEBUG_API') !== '0'
  } catch {
    return true
  }
}

function timerNow(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

/** Start stamp for timing a raw-fetch API call (uploads, exports, …). */
export function apiCallStart(): number {
  return timerNow()
}

function elapsedMs(startedAt: number): number {
  return Math.max(0, Math.round(timerNow() - startedAt))
}

/** Log a completed API call: `[api] GET /api/tenants → 200 (84ms)`. */
export function logApiCall(method: string, path: string, status: number, startedAt: number): void {
  if (!apiLoggingEnabled()) return
  console.log(`[api] ${method} ${path} → ${status} (${elapsedMs(startedAt)}ms)`)
}

/** Log a failed API call with the server's error detail, via console.error.
 * When the backend sent a request_id, it is appended as `(error id: req_…)`
 * so the line can be matched to exactly one backend log line / error row. */
export function logApiError(
  method: string,
  path: string,
  status: number,
  detail: string,
  startedAt: number,
  requestId?: string | null,
): void {
  if (!apiLoggingEnabled()) return
  const idPart = requestId ? ` (error id: ${requestId})` : ''
  console.error(`[api] ${method} ${path} → ${status} (${elapsedMs(startedAt)}ms) — ${detail}${idPart}`)
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private mode — the session simply does not persist */
  }
}

export function getActiveTenantId(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY)
  } catch {
    return null
  }
}

export function setActiveTenantId(tenantId: string | null) {
  try {
    if (tenantId) localStorage.setItem(TENANT_KEY, tenantId)
    else localStorage.removeItem(TENANT_KEY)
  } catch {
    /* ignore */
  }
}

type Unauthorized = () => void

let onUnauthorized: Unauthorized = () => {}

export function setUnauthorizedHandler(fn: Unauthorized) {
  onUnauthorized = fn
}

/** Detail text plus the backend request_id (body field first, then the
 * X-Request-Id header the Phase-1 middleware stamps on every response). */
async function readErrorInfo(
  res: Response,
): Promise<{ detail: string; requestId: string | null }> {
  const headerId = res.headers?.get?.('X-Request-Id') ?? null
  try {
    const body = await res.json()
    const requestId =
      typeof body?.request_id === 'string' && body.request_id ? body.request_id : headerId
    const detail = body?.detail
    if (typeof detail === 'string') return { detail, requestId }
    if (Array.isArray(detail) && detail.length) {
      // pydantic validation errors
      return {
        detail: detail
          .map((d: { loc?: (string | number)[]; msg?: string }) =>
            `${(d.loc ?? []).slice(1).join('.')}: ${d.msg ?? 'invalid'}`,
          )
          .join('; '),
        requestId,
      }
    }
    if (detail) return { detail: JSON.stringify(detail), requestId }
    return { detail: body?.error || res.statusText || `Request failed (${res.status})`, requestId }
  } catch {
    return { detail: res.statusText || `Request failed (${res.status})`, requestId: headerId }
  }
}

function withErrorId(detail: string, requestId: string | null): string {
  return requestId ? `${detail} (error id: ${requestId})` : detail
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  /** Skip the bearer token (used by the login and bootstrap calls). */
  anonymous?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, anonymous } = options
  const startedAt = timerNow()
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  if (!anonymous) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err
    const detail = 'Cannot reach the backend. Is it running on port 9000?'
    logApiError(method, path, 0, detail, startedAt)
    throw new ApiError(0, detail)
  }

  if (res.status === 401) {
    if (!anonymous) onUnauthorized()
    const { detail, requestId } = await readErrorInfo(res)
    logApiError(method, path, 401, detail, startedAt, requestId)
    throw new ApiError(401, withErrorId(detail, requestId), requestId ?? undefined)
  }
  if (!res.ok) {
    const { detail, requestId } = await readErrorInfo(res)
    logApiError(method, path, res.status, detail, startedAt, requestId)
    throw new ApiError(res.status, withErrorId(detail, requestId), requestId ?? undefined)
  }
  logApiCall(method, path, res.status, startedAt)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** Multipart upload: the browser sets the boundary Content-Type. */
export async function postForm<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  const startedAt = timerNow()
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(path, { method: 'POST', headers, body: form, signal })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err
    const detail = 'Cannot reach the backend. Is it running on port 9000?'
    logApiError('POST', path, 0, detail, startedAt)
    throw new ApiError(0, detail)
  }
  if (res.status === 401) {
    onUnauthorized()
    const { detail, requestId } = await readErrorInfo(res)
    logApiError('POST', path, 401, detail, startedAt, requestId)
    throw new ApiError(401, withErrorId(detail, requestId), requestId ?? undefined)
  }
  if (!res.ok) {
    const { detail, requestId } = await readErrorInfo(res)
    logApiError('POST', path, res.status, detail, startedAt, requestId)
    throw new ApiError(res.status, withErrorId(detail, requestId), requestId ?? undefined)
  }
  logApiCall('POST', path, res.status, startedAt)
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T,>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  postForm,
  post: <T,>(path: string, body?: unknown, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: 'POST', body }),
  put: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  del: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
}
