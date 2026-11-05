/**
 * PDF intake — upload a client PDF and place every item where it belongs.
 *
 * `preview` parses the document and reports where each item would land without
 * writing anything. `publish` sends back the reviewed draft, and that is the
 * only call that touches the tenant.
 *
 * Uploads go up as multipart, not base64: a 20 MB PDF becomes ~27 MB once
 * base64-encoded, which is a third of the payload spent on nothing.
 */

import { api, postForm } from './api'
import type { StagedOnboardingDraft } from './uploads'

const base = (tenantId: string) =>
  `/api/admin/tenants/${encodeURIComponent(tenantId)}/pdf-intake`

/** Keep in step with `MAX_UPLOAD_BYTES` in backend/routes/admin.py. */
export const MAX_PDF_BYTES = 20 * 1024 * 1024

export interface PlacementInfo {
  /** Human label, e.g. "Services". */
  label: string
  /** Where it is written, e.g. "Offerings → category “Services”". */
  destination: string
}

export type PlacementMap = Record<string, PlacementInfo>
export type PlacementCounts = Record<string, number>

export interface PdfIntakePreview {
  draft: StagedOnboardingDraft & {
    placement?: PlacementCounts
    warnings?: string[]
  }
  warnings: string[]
  placements: PlacementMap
  placement: PlacementCounts
  /** False when the draft failed schema validation — publish would still work,
   *  but the offending values are reported in `warnings`. */
  profile_valid: boolean
  files: string[]
}

export interface PdfIntakeSummary {
  tenant_id: string
  vertical: string
  brand: string
  published: boolean
  version: number | null
  products_created: number
  products_updated: number
  services_created: number
  faqs_created: number
  kb_chunks_indexed: number
  menu_buttons_count: number
  warnings: string[]
}

export const pdfIntakeApi = {
  preview: (tenantId: string, files: File[], signal?: AbortSignal) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return postForm<PdfIntakePreview>(`${base(tenantId)}/preview`, form, signal)
  },

  publish: (tenantId: string, draft: StagedOnboardingDraft) =>
    api.post<{ status: string; summary: PdfIntakeSummary }>(
      `${base(tenantId)}/publish`,
      { draft },
    ),
}
