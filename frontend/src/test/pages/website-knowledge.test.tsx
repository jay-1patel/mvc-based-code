import { beforeAll, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp, waitForScreen } from '../helpers'
import { testState } from '../fixture-server'
import { ALL_PERMISSIONS } from '@/lib/permissions'

/**
 * Website Knowledge screen: the crawl half of onboarding. Runs list,
 * staged KB/FAQ/product tabs, export downloads, approve — and the super
 * admin's view-only limits.
 */

const RUN = {
  id: 7,
  tenant_id: 'acme',
  source_url: 'https://acme.example/',
  status: 'done',
  trigger: 'manual',
  pages_found: 6,
  pages_scraped: 6,
  error: null,
  created_by: 'acme-admin',
  created_at: '2026-11-10 10:00:00',
}

const RUNS = {
  ok: true,
  tenant_id: 'acme',
  latest: RUN,
  history: [RUN],
  schedule: null,
}

function stagedBody(status: string) {
  return {
    ok: true,
    run_id: 7,
    tenant_id: 'acme',
    status,
    decided_by: status === 'staged' ? null : 'acme-admin',
    decided_at: status === 'staged' ? null : '2026-11-10 11:00:00',
    summary: {
      brand: 'Acme',
      vertical: 'ecommerce',
      offerings_count: 1,
      faqs_count: 1,
      pages_found: 6,
      pages_scraped: 6,
      pages: [],
    },
    payload: {
      bifurcation: {
        knowledge_articles: [
          {
            title: 'About Acme',
            category: 'Company',
            markdown_content: '# About Acme\n\nAcme sells the stuff you need.',
          },
        ],
        faqs: [
          { question: 'Do you deliver?', answer: 'Yes, within 3 days.', category: 'Delivery' },
        ],
        offerings: [
          {
            name: 'Red Shoes',
            category: 'Fashion',
            price: '₹999',
            short_description: '',
            description: 'Bright red shoes.',
          },
        ],
      },
      crawl: {
        root_url: 'https://acme.example/',
        pages: [{ url: 'https://acme.example/', title: 'Home' }],
      },
    },
  }
}

const ADMIN_IDENTITY = {
  id: 2,
  username: 'acme-admin',
  role: 'admin',
  email: 'admin@acme.example',
  permissions: Object.fromEntries(ALL_PERMISSIONS.map((k) => [k, true])),
  tenant_id: 'acme',
  onboarding_approved: true,
}

const SUPER_IDENTITY = {
  id: 1,
  username: 'root',
  role: 'super_admin',
  email: 'root@example.com',
  permissions: Object.fromEntries(ALL_PERMISSIONS.map((k) => [k, true])),
  tenant_id: null,
  onboarding_approved: true,
}

beforeAll(() => {
  // jsdom has no object-URL support; saveBlob only needs the calls to exist.
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
})

function renderPanel(
  identity: typeof ADMIN_IDENTITY | typeof SUPER_IDENTITY,
  runsBody: typeof RUNS = RUNS,
) {
  let stagedStatus = 'staged'
  testState.respond('GET /api/auth/me', () => ({ body: identity }))
  testState.respond('GET /api/admin/tenants/acme/crawl-runs', () => ({ body: runsBody }))
  testState.respond('GET /api/admin/tenants/acme/crawl-runs/7/staged', () => ({
    body: stagedBody(stagedStatus),
  }))
  testState.respond('POST /api/admin/tenants/acme/crawl-runs/7/approve', () => {
    stagedStatus = 'published'
    return { body: { ok: true, result: { status: 'published' } } }
  })
  testState.respond('GET /api/admin/tenants/acme/crawl-runs/7/export', () => ({
    body: 'export-bytes',
  }))
  renderApp('#/tenants/acme/website-knowledge')
  return waitForScreen()
}

describe('Website Knowledge panel', () => {
  it('renders the runs list and the staged knowledge tabs', async () => {
    const user = userEvent.setup()
    await renderPanel(ADMIN_IDENTITY)

    // Header + website from the resolved profile fixture.
    expect(await screen.findByRole('heading', { name: 'Website Knowledge' })).toBeTruthy()
    expect(screen.getAllByText(/acme\.example/).length).toBeGreaterThan(0)

    // Run row: status + pages. Crawls are unlimited, so a finished run that
    // fetched everything it found reads "6 pages crawled" (no "of" framing).
    expect(screen.getByText('done')).toBeTruthy()
    expect(screen.getByText(/6 pages crawled/)).toBeTruthy()
    expect(screen.queryByText(/of 6 pages crawled/)).toBeNull()

    // KB tab is the default: the staged article shows.
    expect(await screen.findByText('About Acme')).toBeTruthy()
    expect(screen.getByText(/Acme sells the stuff you need/)).toBeTruthy()

    // FAQs tab.
    await user.click(screen.getByRole('button', { name: /FAQs \(1\)/ }))
    expect(screen.getByText('Do you deliver?')).toBeTruthy()
    expect(screen.getByText('Yes, within 3 days.')).toBeTruthy()

    // Product Knowledge tab.
    await user.click(screen.getByRole('button', { name: /Product Knowledge \(1\)/ }))
    expect(screen.getByText('Red Shoes')).toBeTruthy()
    expect(screen.getByText('Bright red shoes.')).toBeTruthy()

    // Rule-1 note is visible: the PDF owns menu/brand/hours.
    expect(screen.getByText(/Your PDF stays in charge/)).toBeTruthy()
  })

  it('historic capped runs keep their recorded "X of Y" framing', async () => {
    // Runs recorded under the old 50-page cap keep the counts they were
    // stored with; only complete unlimited runs drop the "of" framing.
    const cappedRun = {
      ...RUN,
      id: 3,
      pages_found: 95,
      pages_scraped: 50,
      created_at: '2026-11-09 09:00:00',
    }
    await renderPanel(ADMIN_IDENTITY, { ...RUNS, history: [RUN, cappedRun] })
    expect(await screen.findByText(/50 of 95 pages crawled/)).toBeTruthy()
    expect(screen.getByText(/6 pages crawled/)).toBeTruthy()
  })

  it('download buttons call the export endpoint with the chosen format', async () => {
    const user = userEvent.setup()
    await renderPanel(ADMIN_IDENTITY)
    expect(await screen.findByText('About Acme')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'CSV' }))
    await waitFor(() => {
      expect(
        testState.calls.some(
          (c) => c.method === 'GET' && c.path.includes('/crawl-runs/7/export?format=csv'),
        ),
      ).toBe(true)
    })

    await user.click(screen.getByRole('button', { name: 'PDF' }))
    await waitFor(() => {
      expect(
        testState.calls.some(
          (c) => c.method === 'GET' && c.path.includes('/crawl-runs/7/export?format=pdf'),
        ),
      ).toBe(true)
    })

    await user.click(screen.getByRole('button', { name: 'JSON' }))
    await waitFor(() => {
      expect(
        testState.calls.some(
          (c) => c.method === 'GET' && c.path.includes('/crawl-runs/7/export?format=json'),
        ),
      ).toBe(true)
    })
  })

  it('approve publishes and shows the published state', async () => {
    const user = userEvent.setup()
    await renderPanel(ADMIN_IDENTITY)
    expect(await screen.findByText('About Acme')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: /Approve & publish knowledge/ }))

    await waitFor(() => {
      expect(
        testState.calls.some(
          (c) => c.method === 'POST' && c.path.endsWith('/crawl-runs/7/approve'),
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/Published to your bot's knowledge/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Approve & publish knowledge/ })).toBeNull()
  })

  it('super admin is view-only: no start, no approve', async () => {
    await renderPanel(SUPER_IDENTITY)
    expect(await screen.findByText('About Acme')).toBeTruthy()

    const start = screen.getByRole('button', { name: /Start crawl/ }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    expect(screen.queryByRole('button', { name: /Approve & publish knowledge/ })).toBeNull()
    expect(screen.getAllByText(/View-only/).length).toBeGreaterThan(0)
  })

  it('shows the empty state when there are no crawls', async () => {
    testState.respond('GET /api/auth/me', () => ({ body: ADMIN_IDENTITY }))
    testState.respond('GET /api/admin/tenants/acme/crawl-runs', () => ({
      body: { ok: true, tenant_id: 'acme', latest: null, history: [], schedule: null },
    }))
    renderApp('#/tenants/acme/website-knowledge')
    await waitForScreen()
    expect(await screen.findByText('No crawls yet')).toBeTruthy()
  })
})
