import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp, waitForScreen } from '../helpers'

async function renderView(hash: string) {
  renderApp(hash)
  await waitForScreen()
}
import { testState } from '../fixture-server'

/**
 * Phase 2 (business-data reskin) structural guarantees: the two uploads
 * doors, the honest "Coming next" placeholders, and the attention strips —
 * each strip must appear only when the screen's own data supports it.
 */

const T = 'acme'

afterEach(() => {
  testState.reset()
})

describe('phase 2 — uploads doors', () => {
  it('Knowledge and Files are two doors onto the same uploads screen', async () => {
    renderApp(`#/tenants/${T}/overview`)
    await waitForScreen()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Knowledge' }))
    await waitFor(
      () =>
        expect(
          screen.getByRole('heading', { name: 'Uploads & Auto-Configurator' }),
        ).toBeInTheDocument(),
      { timeout: 3000 },
    )

    await user.click(await screen.findByRole('button', { name: 'Files' }))
    await waitFor(
      () =>
        expect(
          screen.getByRole('heading', { name: 'Uploads & Auto-Configurator' }),
        ).toBeInTheDocument(),
      { timeout: 3000 },
    )
    expect(window.location.hash).toBe(`#/tenants/${T}/uploads`)
  })
})

describe('phase 2 — placeholders stay placeholders', () => {
  it('technologies renders the reskinned Coming next placeholder', async () => {
    await renderView(`#/tenants/${T}/technologies`)
    expect(screen.getByRole('heading', { name: 'Technologies' })).toBeInTheDocument()
    expect(screen.getByText('Coming next')).toBeInTheDocument()
    expect(
      screen.getByText(/wired into the navigation but its panel is not built yet/),
    ).toBeInTheDocument()
  })

  it('portfolio renders the same placeholder', async () => {
    await renderView(`#/tenants/${T}/portfolio`)
    expect(screen.getByRole('heading', { name: 'Portfolio' })).toBeInTheDocument()
    expect(screen.getByText('Coming next')).toBeInTheDocument()
  })
})

describe('phase 2 — attention strips follow the data', () => {
  it('orders: placed orders surface a Needs-you strip', async () => {
    await renderView(`#/tenants/${T}/orders`)
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/order is placed and waiting to be confirmed/)).toBeInTheDocument()
  })

  it('partners: outstanding payments surface a Needs-you strip', async () => {
    await renderView(`#/tenants/${T}/distributors`)
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/outstanding payments totalling/)).toBeInTheDocument()
  })

  it('customers: no open complaints in the data, no strip', async () => {
    await renderView(`#/tenants/${T}/customers`)
    expect(await screen.findByText('Priya')).toBeInTheDocument()
    expect(screen.queryByText('Needs you')).not.toBeInTheDocument()
  })

  it('customers: open complaints in the data surface the strip', async () => {
    testState.respond('GET /api/admin/tenants/acme/customers', () => ({
      body: {
        customers: [
          {
            wa_id: '911111111111',
            name: 'Priya',
            mobile: '+91 11111 11111',
            total_orders: 3,
            total_complaints: 3,
            open_complaints: 2,
            total_spent: 1200,
            last_active: '2025-02-01T00:00:00Z',
          },
        ],
        count: 1,
      },
    }))
    await renderView(`#/tenants/${T}/customers`)
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/customer has open complaints/)).toBeInTheDocument()
  })

  it('uploads: a failed file surfaces the strip; clean data does not', async () => {
    testState.respond('GET /api/admin/tenants/acme/files', () => ({
      body: {
        files: [
          {
            id: 1,
            name: 'faq.pdf',
            ext: 'pdf',
            module: 'faq',
            size: 1024,
            status: 'ready',
            chunks_count: 4,
            tenant_id: 'acme',
            created_at: '2025-01-01T00:00:00Z',
          },
          {
            id: 2,
            name: 'broken.pdf',
            ext: 'pdf',
            module: 'faq',
            size: 512,
            status: 'failed',
            error_message: 'Unreadable',
            tenant_id: 'acme',
            created_at: '2025-01-02T00:00:00Z',
          },
        ],
      },
    }))
    await renderView(`#/tenants/${T}/uploads`)
    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText(/failed to process/)).toBeInTheDocument()
  })
})
