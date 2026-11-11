/**
 * WhatsApp Tester — drives POST /api/test-simulator (capture-only).
 * Same orchestrator as production; outbound messages are captured, not sent via Send2.
 */

import { api } from './api'

export type SimulatorUserType = 'b2c' | 'b2b' | 'auto'

export interface CapturedMessage {
  type?: string
  text?: string
  body?: string
  header?: string
  button_text?: string
  buttons?: Array<{ id?: string; title?: string; reply?: { id?: string; title?: string } }>
  sections?: Array<{
    title?: string
    rows?: Array<{ id?: string; title?: string; description?: string }>
  }>
  sent_live?: boolean
  [key: string]: unknown
}

export interface SimulatorResult {
  messages?: CapturedMessage[]
  new_state?: string | null
  human_handover?: boolean
  user_type?: string
  status?: string
  error?: string | null
}

export const testSimulatorApi = {
  send: (body: {
    wa_id: string
    message: string
    user_type?: SimulatorUserType
    tenant_id?: string
  }) => api.post<SimulatorResult>('/api/test-simulator', body),

  reset: (body: { wa_id: string; tenant_id?: string; user_type?: SimulatorUserType }) =>
    api.post<SimulatorResult>('/api/test-simulator/reset', body),

  menuPreview: (body: {
    wa_id: string
    user_type?: 'b2c' | 'b2b'
    tenant_id?: string
    tier?: string
    outstanding?: number
  }) => api.post<Record<string, unknown>>('/api/test-simulator/menu-preview', body),
}
