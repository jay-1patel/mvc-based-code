/**
 * Tenant conversations — chat history, the live inbox and complaints.
 *
 * Every route is tenant-scoped: the tenant is a path segment and the server
 * refuses a token that belongs to another one. These read the same
 * `chat_history` the bot writes, filtered to this tenant.
 */

import { api } from './api'
import type { Attachment } from './attachments'
import type { Agent, ChatTurn, Complaint, ConversationThread, InboxItem } from './types'

const base = (tenantId: string) => `/api/admin/tenants/${encodeURIComponent(tenantId)}`

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const tail = search.toString()
  return tail ? `?${tail}` : ''
}

/** `[faq] hello` -> `hello`; a row with no prefix is returned unchanged. */
export function stripRoute(message: string): string {
  return message.startsWith('[') && message.includes('] ')
    ? message.slice(message.indexOf('] ') + 2)
    : message
}

export const conversationsApi = {
  threads: (
    tenantId: string,
    opts: { search?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api
      .get<{ conversations: ConversationThread[]; count: number }>(
        `${base(tenantId)}/conversations${query(opts)}`,
        signal,
      )
      .then((r) => r.conversations),

  thread: (tenantId: string, waId: string, limit = 300, signal?: AbortSignal) =>
    api.get<{ wa_id: string; messages: ChatTurn[] }>(
      `${base(tenantId)}/conversations/${encodeURIComponent(waId)}${query({ limit })}`,
      signal,
    ),

  history: (
    tenantId: string,
    opts: { days?: number; search?: string; waId?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api
      .get<{ history: ChatTurn[] }>(`${base(tenantId)}/chat-history${query(opts)}`, signal)
      .then((r) => r.history),

  inbox: (tenantId: string, includeResolved = false, signal?: AbortSignal) =>
    api.get<{ queue: InboxItem[]; count: number; handoff_count: number }>(
      `${base(tenantId)}/inbox${query({ include_resolved: includeResolved })}`,
      signal,
    ),

  setHandover: (tenantId: string, waId: string, mode: 'bot' | 'human') =>
    api.put<{ status: string }>(`${base(tenantId)}/inbox/handover/${encodeURIComponent(waId)}`, { mode }),

  assign: (tenantId: string, waId: string, agentId: string) =>
    api.post<{ status: string }>(`${base(tenantId)}/inbox/assign`, { wa_id: waId, agent_id: agentId }),

  resolve: (tenantId: string, waId: string, reply = true) =>
    api.post<{ status: string; notified: boolean }>(
      `${base(tenantId)}/inbox/resolve/${encodeURIComponent(waId)}${query({ reply })}`,
    ),

  reply: (tenantId: string, waId: string, message: string, attachment?: Attachment | null) =>
    api.post<{ status: string; sent: boolean }>(
      `${base(tenantId)}/inbox/${encodeURIComponent(waId)}/reply`,
      { message, attachment: attachment ?? undefined },
    ),

  agents: (tenantId: string, signal?: AbortSignal) =>
    api.get<{ agents: Agent[] }>(`${base(tenantId)}/agents`, signal).then((r) => r.agents),

  complaints: (
    tenantId: string,
    opts: { status?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) =>
    api.get<{ complaints: Complaint[]; count: number; open_count: number }>(
      `${base(tenantId)}/complaints${query(opts)}`,
      signal,
    ),

  updateComplaint: (
    tenantId: string,
    ticketId: string,
    body: { status?: string; priority?: string; assigned_to?: string },
  ) => api.put<{ status: string }>(`${base(tenantId)}/complaints/${encodeURIComponent(ticketId)}`, body),

  replyComplaint: (tenantId: string, ticketId: string, message: string, status?: string) =>
    api.post<{ status: string; sent: boolean; new_status: string | null }>(
      `${base(tenantId)}/complaints/${encodeURIComponent(ticketId)}/reply`,
      { message, status },
    ),

  deleteComplaint: (tenantId: string, ticketId: string) =>
    api.del<{ status: string }>(`${base(tenantId)}/complaints/${encodeURIComponent(ticketId)}`),
}

export const COMPLAINT_STATUSES = ['open', 'in_progress', 'awaiting_info', 'resolved', 'closed']
export const COMPLAINT_PRIORITIES = ['low', 'normal', 'high', 'urgent']
