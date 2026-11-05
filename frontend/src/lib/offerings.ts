/**
 * Tenant-owned offerings — the content panel behind the sidebar's per-vertical
 * entry ("Services" for a software tenant, "Packages" for a travel tenant).
 *
 * Every route is tenant-scoped on the server: the tenant is a path segment, and
 * `require_tenant_access()` refuses a token that belongs to another tenant. The
 * console never sends a tenant id in a body or query for a read or a write.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Offering, OfferingInput, RecordColumn, RecordColumnType, RecordSchema } from './types'

const base = (tenantId: string) => `/api/admin/tenants/${encodeURIComponent(tenantId)}/offerings`
const schemaBase = (tenantId: string) =>
  `/api/admin/tenants/${encodeURIComponent(tenantId)}/record-schema`

export interface ColumnInput {
  key: string
  label: string
  type: RecordColumnType
  required?: boolean
  options?: string[]
  help?: string
}

export const offeringsApi = {
  list: (tenantId: string, opts: { includeInactive?: boolean } = {}, signal?: AbortSignal) =>
    api
      .get<{ count: number; offerings: Offering[] }>(
        `${base(tenantId)}?include_inactive=${opts.includeInactive ? 'true' : 'false'}`,
        signal,
      )
      .then((r) => r.offerings),

  /** Rows plus the tenant's current column schema, in one round trip. */
  listDetailed: (
    tenantId: string,
    opts: { includeInactive?: boolean } = {},
    signal?: AbortSignal,
  ) =>
    api.get<{ count: number; columns: RecordColumn[]; offerings: Offering[] }>(
      `${base(tenantId)}?include_inactive=${opts.includeInactive ? 'true' : 'false'}`,
      signal,
    ),

  create: (tenantId: string, body: OfferingInput) =>
    api.post<{ status: string; offering: Offering }>(base(tenantId), body).then((r) => r.offering),

  update: (tenantId: string, id: number, body: Partial<OfferingInput>) =>
    api
      .put<{ status: string; offering: Offering }>(`${base(tenantId)}/${id}`, body)
      .then((r) => r.offering),

  remove: (tenantId: string, id: number, hard = false) =>
    api.del<{ status: string; id: number }>(`${base(tenantId)}/${id}?hard=${hard ? 'true' : 'false'}`),

  // ── column schema ──────────────────────────────────────────────────────
  // Each mutation returns the full, freshly reconciled column list.

  getSchema: (tenantId: string, signal?: AbortSignal) =>
    api.get<RecordSchema>(schemaBase(tenantId), signal).then((r) => r.columns),

  createColumn: (tenantId: string, body: ColumnInput) =>
    api
      .post<{ status: string; column: RecordColumn; columns: RecordColumn[] }>(
        `${schemaBase(tenantId)}/columns`,
        body,
      )
      .then((r) => r.columns),

  updateColumn: (tenantId: string, key: string, body: Partial<ColumnInput> & { sort_order?: number }) =>
    api
      .put<{ status: string; column: RecordColumn; columns: RecordColumn[] }>(
        `${schemaBase(tenantId)}/columns/${encodeURIComponent(key)}`,
        body,
      )
      .then((r) => r.columns),

  deleteColumn: (tenantId: string, key: string) =>
    api.del<{ status: string; key: string; database_column_dropped: boolean; columns: RecordColumn[] }>(
      `${schemaBase(tenantId)}/columns/${encodeURIComponent(key)}`,
    ),

  resetColumns: (tenantId: string) =>
    api
      .post<{ status: string; vertical: string; columns: RecordColumn[] }>(`${schemaBase(tenantId)}/reset`)
      .then((r) => r.columns),
}

export interface OfferingsState {
  offerings: Offering[]
  loading: boolean
  error: string | null
  reload: () => void
}

/** Live count for the sidebar badge, tolerant of a 403 (no offerings perms). */
export function useOfferingsCount(tenantId: string | null): number | null {
  const [count, setCount] = useState<number | null>(null)

  useEffect(() => {
    if (!tenantId) {
      setCount(null)
      return
    }
    const controller = new AbortController()
    offeringsApi
      .list(tenantId, {}, controller.signal)
      .then((rows) => setCount(rows.filter((o) => o.is_active).length))
      .catch(() => setCount(null))
    return () => controller.abort()
  }, [tenantId])

  return count
}

export function useOfferings(tenantId: string, includeInactive: boolean): OfferingsState {
  const [offerings, setOfferings] = useState<Offering[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true)
      offeringsApi
        .list(tenantId, { includeInactive }, signal)
        .then((rows) => {
          setOfferings(rows)
          setError(null)
        })
        .catch((err: Error) => {
          if (err.name === 'AbortError') return
          setError(err.message)
        })
        .finally(() => setLoading(false))
    },
    [tenantId, includeInactive],
  )

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  return { offerings, loading, error, reload: () => load() }
}
