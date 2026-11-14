/**
 * WhatsApp Tester — client for the backend test simulator
 * (routes/test_simulator.py, mounted at /api/test-simulator).
 *
 * The simulator runs the SAME orchestrator pipeline as a real WhatsApp
 * connection with capture-only on: outgoing messages are captured instead
 * of being sent through send2.digital, and returned to the console so the
 * Tester page (components/tester/whatsapp-tester.tsx) can render exactly
 * what a customer would receive.
 *
 * This module was missing from the repo — the Tester page imported it,
 * but the client itself was never committed. It is written here against
 * the backend contract: POST /api/test-simulator {wa_id, message,
 * user_type, tenant_id} and POST /api/test-simulator/reset {wa_id, ...}.
 */
import { api } from './api'

export type SimulatorUserType = 'b2c' | 'b2b' | 'auto'

export interface CapturedButton {
  id?: string
  title?: string
  reply?: { id?: string; title?: string }
}

export interface CapturedListRow {
  id?: string
  title?: string
  description?: string
}

export interface CapturedSection {
  title?: string
  rows?: CapturedListRow[]
}

export interface CapturedMessage {
  type?: string
  text?: string
  body?: string
  header?: string
  button_text?: string
  buttons?: CapturedButton[]
  sections?: CapturedSection[]
  [key: string]: unknown
}

/**
 * Phone messages — the delivery-shaped rendering of the captured payloads
 * (backend: services.whatsapp_render.render_phone_messages). This is the
 * exact sequence a WhatsApp user receives: formatting already normalized
 * to WhatsApp markup, interactive limits applied, over-long bodies split.
 * The replica console renders THESE, never the raw captures.
 */
export type PhoneMessage =
  | { kind: 'text'; text: string; source_index: number }
  | { kind: 'image'; media_url: string; source_index: number }
  | { kind: 'document'; media_url: string; filename?: string; source_index: number }
  | {
      kind: 'buttons'
      text: string
      buttons: { id: string; title: string }[]
      source_index: number
    }
  | {
      kind: 'list'
      header: string
      body: string
      footer?: string
      button_text: string
      sections: CapturedSection[]
      source_index: number
    }
  | {
      kind: 'cta_url'
      text: string
      display_text: string
      url: string
      image_url?: string
      footer?: string
      source_index: number
    }

export interface SimulatorResult {
  messages: CapturedMessage[]
  /** Delivery-shaped phone messages; render these in the replica console. */
  whatsapp?: PhoneMessage[]
  status?: string
  new_state?: string | null
  user_type?: string
  human_handover?: boolean
  [key: string]: unknown
}

export interface SimulatorResetResult {
  wa_id: string
  reset: boolean
  new_state: null
  human_handover: boolean
  status: string
}

export interface SimulatorSendInput {
  wa_id: string
  message: string
  user_type?: SimulatorUserType
  tenant_id?: string
}

export interface SimulatorResetInput {
  wa_id: string
  user_type?: SimulatorUserType
  tenant_id?: string
}

export const testSimulatorApi = {
  send: (input: SimulatorSendInput) =>
    api.post<SimulatorResult>('/api/test-simulator', input),
  reset: (input: SimulatorResetInput) =>
    api.post<SimulatorResetResult>('/api/test-simulator/reset', input),
}
