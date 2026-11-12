import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp, waitForScreen } from '../helpers'
import { testState } from '../fixture-server'

const T = 'acme'

afterEach(() => {
  testState.reset()
})

async function renderView(hash: string) {
  renderApp(hash)
  await waitForScreen()
}

/**
 * Phase 3 (comms + grow reskin) structural guarantees: the reskinned views
 * render under their plain nav names, the attention strips follow the
 * loaded data exactly (strip when open work exists, none when it does
 * not), and the standalone callback route stays the honest placeholder.
 */
describe('phase 3 — comms views in the remake skin', () => {
  it('past chats: conversation list opens the transcript with bot caption', async () => {
    await renderView(`#/tenants/${T}/chat-history`)
    expect(screen.getByRole('heading', { name: 'Past chats' })).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Priya/ }))
    expect(await screen.findByText('I need help')).toBeInTheDocument()
    expect(screen.getByText('How can I help?')).toBeInTheDocument()
    expect(screen.getByText('Bot · faq')).toBeInTheDocument()
  })

  it('help requests: an open complaint surfaces the Needs-you strip', async () => {
    await renderView(`#/tenants/${T}/complaints`)
    expect(screen.getByRole('heading', { name: 'Help requests' })).toBeInTheDocument()
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/complaint is still open/)).toBeInTheDocument()
  })

  it('help requests: no complaints in the data, no strip', async () => {
    testState.respond('GET /api/admin/tenants/acme/complaints', () => ({
      body: { complaints: [], count: 0, open_count: 0 },
    }))
    await renderView(`#/tenants/${T}/complaints`)
    expect(await screen.findByText('No complaints')).toBeInTheDocument()
    expect(screen.queryByText('Needs you')).not.toBeInTheDocument()
  })

  it('callbacks: an open request surfaces the Needs-you strip', async () => {
    // The callbacks API lives on /api/tenants (not /api/admin/tenants) and
    // the fixture server has no stub for it — register the pending request
    // explicitly.
    testState.respond('GET /api/tenants/acme/callbacks', () => ({
      body: {
        callbacks: [
          {
            id: 'cb-1',
            tenant_id: 'acme',
            wa_id: '919876543210',
            customer_name: 'Priya Patel',
            customer_email: null,
            customer_phone: '+91 98765 43210',
            callback_type: 'support',
            priority: 'high',
            preferred_date: '2026-10-12',
            preferred_time: '10:00',
            purpose: 'Demo of the dashboard',
            additional_info: '',
            assigned_agent_id: null,
            assigned_agent_name: null,
            meet_link: null,
            status: 'pending',
            scheduled_start_time: null,
            scheduled_end_time: null,
            created_at: '2026-10-10T09:00:00Z',
            updated_at: '2026-10-10T09:00:00Z',
          },
        ],
        total_count: 1,
      },
    }))
    await renderView(`#/tenants/${T}/callbacks`)
    expect(screen.getByRole('heading', { name: 'Callbacks' })).toBeInTheDocument()
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/callback is still open/)).toBeInTheDocument()
  })

  it('callbacks: a completed-only list shows no strip', async () => {
    testState.respond('GET /api/tenants/acme/callbacks', () => ({
      body: {
        callbacks: [
          {
            id: 'cb-9',
            tenant_id: 'acme',
            wa_id: '919876543210',
            customer_name: 'Done Person',
            customer_email: null,
            customer_phone: null,
            callback_type: 'support',
            priority: 'medium',
            preferred_date: '',
            preferred_time: '',
            purpose: 'Finished',
            additional_info: '',
            assigned_agent_id: null,
            assigned_agent_name: null,
            meet_link: null,
            status: 'completed',
            scheduled_start_time: null,
            scheduled_end_time: null,
            created_at: '2026-10-10T09:00:00Z',
            updated_at: '2026-10-10T09:00:00Z',
          },
        ],
        total_count: 1,
      },
    }))
    await renderView(`#/tenants/${T}/callbacks`)
    expect(await screen.findByText('Done Person')).toBeInTheDocument()
    expect(screen.queryByText('Needs you')).not.toBeInTheDocument()
  })
})

describe('phase 3 — grow + advanced views in the remake skin', () => {
  it('campaigns: table renders with the campaign and its status', async () => {
    await renderView(`#/tenants/${T}/campaigns`)
    expect(screen.getByRole('heading', { name: 'Send an offer' })).toBeInTheDocument()
    expect(await screen.findByText('Diwali blast')).toBeInTheDocument()
    expect(screen.getByText('draft')).toBeInTheDocument()
  })

  it('intents: editor renders under its plain name', async () => {
    await renderView(`#/tenants/${T}/intents`)
    expect(
      await screen.findByRole('heading', { name: 'Questions the bot understands' }),
    ).toBeInTheDocument()
    expect(screen.getByText('All intents')).toBeInTheDocument()
  })

  it('layers: selector renders with the merged view and version pill', async () => {
    await renderView(`#/tenants/${T}/layers`)
    expect(await screen.findByRole('heading', { name: 'Layers' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Effective \(merged\)/ })).toBeInTheDocument()
    expect(screen.getByText('current v2')).toBeInTheDocument()
  })

  it('callback detail route stays the Coming next placeholder', async () => {
    await renderView(`#/tenants/${T}/callback`)
    expect(screen.getByRole('heading', { name: 'Callback' })).toBeInTheDocument()
    expect(screen.getByText('Coming next')).toBeInTheDocument()
  })
})
