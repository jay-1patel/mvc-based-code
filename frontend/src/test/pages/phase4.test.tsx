import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp, waitForScreen } from '../helpers'
import { testState } from '../fixture-server'
import { ALL_PERMISSIONS, permissionGroups } from '@/lib/permissions'

const T = 'acme'

afterEach(() => {
  testState.reset()
})

async function renderView(hash: string) {
  renderApp(hash)
  await waitForScreen()
}

/**
 * Phase 4 (manage + super views + auth surfaces) structural guarantees:
 * the People screen keeps its presets and has NO role-change control
 * (roles are fixed at creation — the role is a pill, never an editor),
 * the wizard keeps its two shapes (super = Company only, admin = all
 * seven steps), and the approval queues / activity log / errors page
 * render in the remake skin with their actions intact.
 */
describe('phase 4 — people (team)', () => {
  it('create form renders the four sub-admin presets and a preset pre-ticks switches', async () => {
    await renderView('#/team')
    expect(await screen.findByText('helper')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New admin' }))

    for (const label of ['Support', 'Content', 'Operations', 'Growth']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    // The permission matrix renders every switch of the (vertical-filtered)
    // permission groups for the sub-admin draft.
    const expectedSwitches = permissionGroups(undefined).reduce(
      (n, g) => n + g.permissions.length,
      0,
    )
    expect(screen.getAllByRole('switch')).toHaveLength(expectedSwitches)

    await user.click(screen.getByRole('button', { name: 'Support' }))
    expect(screen.getByText(/Pre-ticked switches/)).toBeInTheDocument()
    expect(
      screen.getAllByRole('switch').some((el) => el.getAttribute('aria-checked') === 'true'),
    ).toBe(true)
  })

  it('editing a member offers presets and tenant scope but NO role-change control', async () => {
    await renderView('#/team')
    expect(await screen.findByText('helper')).toBeInTheDocument()
    // The role is a fixed pill on the card, not a control.
    expect(screen.getByText('sub admin')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    expect(await screen.findByText('Edit helper')).toBeInTheDocument()
    for (const label of ['Support', 'Content', 'Operations', 'Growth']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    // The hard product rule: no control anywhere in the edit form changes
    // a person's role. (The only Role select in this screen belongs to the
    // create form, which is closed.)
    expect(screen.queryByLabelText('Role')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Role' })).not.toBeInTheDocument()
  })
})

describe('phase 4 — register wizard shapes', () => {
  it('super admin gets the Company step only, with the create payload fields', async () => {
    await renderView('#/register')
    expect(screen.getByRole('heading', { name: 'Register a tenant' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Company name/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Tenant id/)).toBeInTheDocument()
    // One step only: the submit is the tenant creation, and no admin-step
    // labels appear in the stepper.
    expect(screen.getByRole('button', { name: 'Create tenant' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /WhatsApp/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Guardrails/ })).not.toBeInTheDocument()
  })

  it('locked admin gets all seven steps in the stepper', async () => {
    testState.respond('GET /api/auth/me', () => ({
      body: {
        id: 2,
        username: 'acme-admin',
        role: 'admin',
        email: 'admin@acme.example',
        permissions: Object.fromEntries(ALL_PERMISSIONS.map((k) => [k, true])),
        tenant_id: T,
        onboarding_approved: false,
      },
    }))
    await renderView('#/register')
    for (const label of ['Tenant', 'WhatsApp', 'Brand & voice', 'Your business', 'Notifications', 'Guardrails', 'Review']) {
      expect(await screen.findByText(label)).toBeInTheDocument()
    }
  })
})

describe('phase 4 — approval queues', () => {
  it('business requests: a pending registration renders with decide actions', async () => {
    testState.respond('GET /api/admin/tenant-change-requests', () => ({
      body: {
        requests: [
          {
            id: 7,
            request_type: 'create_tenant',
            tenant_id: T,
            tenant_display_name: 'Acme Retail',
            tenant_vertical: 'ecommerce',
            tenant_waba_phone_id: null,
            requester_username: 'acme-admin',
            status: 'pending',
            summary: 'Register a tenant',
            payload: {
              tenant: { display_name: 'Acme Retail', vertical: 'ecommerce', status: 'active' },
              snapshot: {},
            },
            created_at: '2025-01-03T10:00:00Z',
          },
        ],
      },
    }))
    await renderView('#/tenant-requests')
    expect(screen.getByRole('heading', { name: 'Business requests' })).toBeInTheDocument()
    expect(await screen.findByText('Register a tenant · acme')).toBeInTheDocument()
    expect(screen.getByText('pending')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve and apply' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject request' })).toBeInTheDocument()
  })

  it('api requests: a pending request renders with eligibility actions', async () => {
    testState.respond('GET /api/admin/integration-requests', () => ({
      body: {
        requests: [
          {
            id: 3,
            api_type: 'payment_api',
            tenant_id: T,
            provider: 'Stripe',
            environment: 'sandbox',
            purpose: 'Checkout payments for WhatsApp orders.',
            status: 'pending',
            requester_username: 'acme-admin',
            created_at: '2025-01-03T10:00:00Z',
          },
        ],
      },
    }))
    await renderView('#/api-requests')
    expect(screen.getByRole('heading', { name: 'API requests' })).toBeInTheDocument()
    expect(await screen.findByText('Approval is not live activation')).toBeInTheDocument()
    expect(screen.getByText('Payment Gateway API · acme')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve eligibility' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject request' })).toBeInTheDocument()
  })
})

describe('phase 4 — activity log & errors', () => {
  it('activity log renders events and the export menu offers CSV/PDF/JSON', async () => {
    testState.respond('GET /api/admin/audit-history', () => ({
      body: {
        events: [
          {
            id: 1,
            created_at: '2025-01-03T10:00:00Z',
            action: 'admin_created',
            actor_username: 'root',
            actor_label: 'root',
            actor_role: 'super_admin',
            tenant_id: T,
            outcome: 'success',
            details: {},
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      },
    }))
    await renderView('#/audit-history')
    expect(screen.getByRole('heading', { name: 'Activity log' })).toBeInTheDocument()
    expect(await screen.findByText('Showing 1–1 of 1 events')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Export' }))
    expect(screen.getByText('Export as CSV')).toBeInTheDocument()
    expect(screen.getByText('Export as PDF')).toBeInTheDocument()
    expect(screen.getByText('Export as JSON')).toBeInTheDocument()
  })

  it('errors & health shows the failed request with a copyable request id', async () => {
    testState.respond('GET /api/admin/errors', () => ({
      body: {
        count: 1,
        errors: [
          {
            id: 1,
            created_at: '2025-01-03T10:00:00Z',
            provider: 'send2',
            api: 'POST /api/tenants/acme/messages',
            severity: 'error',
            message: 'Provider refused the send',
            tenant_id: T,
            request_id: 'req-abc-123',
            http_status: 502,
          },
        ],
      },
    }))
    await renderView('#/errors')
    expect(screen.getByRole('heading', { name: 'Errors & health' })).toBeInTheDocument()
    expect(await screen.findByText('Provider refused the send')).toBeInTheDocument()
    expect(screen.getByText('req-abc-123')).toBeInTheDocument()
    expect(screen.getByText('HTTP 502')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy/ })).toBeInTheDocument()
  })
})

describe('phase 4 — api access & menu editor', () => {
  it('api access renders the provider cards and the requests tab', async () => {
    testState.respond('GET /api/integration-requests', () => ({ body: { requests: [] } }))
    testState.respond('GET /api/tenants/acme/integrations', () => ({ body: { integrations: [] } }))
    await renderView(`#/tenants/${T}/api-access`)
    expect(screen.getByRole('heading', { name: 'API Access & Live Integrations' })).toBeInTheDocument()
    expect(await screen.findByText('Stripe')).toBeInTheDocument()
    expect(screen.getByText('Razorpay')).toBeInTheDocument()
    expect(screen.getByText('Blue Dart')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Configure/ }).length).toBeGreaterThan(0)

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Requests (0)' }))
    expect(await screen.findByText('Submitted Integration Requests (0)')).toBeInTheDocument()
  })

  it('menu editor renders with its draft/publish actions', async () => {
    // Render as the tenant admin: since the QA-audit fixes, write controls
    // in the menu editor are hidden from view-only super admins
    // (canWriteTenant), so the default super-admin fixture would
    // correctly see none of these actions.
    testState.respond('GET /api/auth/me', () => ({
      body: {
        id: 2,
        username: 'acme-admin',
        role: 'admin',
        email: 'admin@acme.example',
        permissions: Object.fromEntries(ALL_PERMISSIONS.map((k) => [k, true])),
        tenant_id: T,
        onboarding_approved: true,
      },
    }))
    await renderView(`#/tenants/${T}/menu-edit`)
    expect(screen.getByRole('heading', { name: 'Menu editor' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Add option' })).toBeInTheDocument()
    // Header actions and the Publishing panel both offer Save & publish.
    expect(screen.getAllByRole('button', { name: 'Save & publish' }).length).toBeGreaterThan(0)
  })
})
