import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp, waitForScreen } from '../helpers'
import { testState } from '../fixture-server'
import { ALL_PERMISSIONS } from '@/lib/permissions'
import {
  applyStagedToDraft,
  draftToSnapshot,
  emptyDraft,
  type WizardDraft,
} from '@/lib/profile'
import type { StagedOnboardingDraft } from '@/lib/uploads'

/**
 * Admin-side wizard PDF auto-fill: the tenant admin (still awaiting their
 * first approval) stages their own company document in the Tenant step and
 * the extracted details pre-fill the later steps — without overwriting
 * anything already there, and without publishing anything.
 */

const STAGED: StagedOnboardingDraft = {
  status: 'staged',
  tenant_id: 'acme',
  vertical: 'ecommerce',
  brand: {
    name: 'Acme Retail',
    bot_name: 'Pdf Bot',
    tagline: 'From the document',
    website: 'https://pdf.example',
    support_email: 'pdf@acme.example',
    support_phone: '+91 11111 11111',
    signature: '— Pdf Team',
  },
  business_hours: {
    timezone: 'Asia/Kolkata',
    open: '10:00',
    close: '20:00',
    open_days: [1, 2, 3, 4, 5],
  },
  menu_items: [
    { id: 'services', title: 'Our Services', description: '', section: '', action: 'services' },
    { id: 'contact', title: 'Contact Us', description: '', section: '', action: 'contact' },
  ],
  offerings: [
    { name: 'Shoes', category: 'Fashion', price: '', short_description: '', description: '' },
    { name: 'Hats', category: 'Fashion', price: '', short_description: '', description: '' },
  ],
  faqs: [],
  knowledge_articles: [],
  guardrails: { tone: 'formal and compliant', never_state: ['We deliver in one hour'] },
  source_files: ['company.pdf'],
  stats: { offerings_count: 2, menu_items_count: 2, faqs_count: 0, articles_count: 0 },
  // A filled onboarding kit: every section was parsed out of the document
  // itself, so the full mapping (tone, hours, guardrails, menu) may apply.
  extraction_mode: 'kit',
}

/**
 * The same payload as a general (universal) extraction. Without per-field
 * provenance, hours/tone/guardrails/menu in such a payload may be template
 * defaults substituted by the extractor — only brand, contacts and the
 * services list are known to come from the document text.
 */
const STAGED_UNIVERSAL: StagedOnboardingDraft = { ...STAGED, extraction_mode: 'universal' }

const ADMIN_IDENTITY = {
  id: 2,
  username: 'acme-admin',
  role: 'admin',
  email: 'admin@acme.example',
  permissions: Object.fromEntries(ALL_PERMISSIONS.map((k) => [k, true])),
  tenant_id: 'acme',
  onboarding_approved: false,
}

function renderLockedAdminWizard() {
  testState.respond('GET /api/auth/me', () => ({ body: ADMIN_IDENTITY }))
  renderApp('#/register')
  return waitForScreen()
}

async function pickTenantAndUpload() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: /Acme Retail/ }))
  const input = (await screen.findByLabelText(/Upload your company document/)) as HTMLInputElement
  await user.upload(input, new File(['%PDF-fake'], 'company.pdf', { type: 'application/pdf' }))
  return user
}

describe('applyStagedToDraft (unit)', () => {
  it('fills empty fields from the staged document and carries the payload', () => {
    const base: WizardDraft = { ...emptyDraft(), tenantId: 'acme', vertical: 'ecommerce' }
    const { draft, filled } = applyStagedToDraft(base, STAGED)
    expect(draft.companyName).toBe('Acme Retail')
    expect(draft.botName).toBe('Pdf Bot')
    expect(draft.supportEmail).toBe('pdf@acme.example')
    expect(draft.salesEmail).toBe('pdf@acme.example')
    expect(draft.openTime).toBe('10:00')
    expect(draft.openDays).toEqual([1, 2, 3, 4, 5])
    expect(draft.alwaysOpen).toBe(false)
    expect(draft.neverState).toBe('We deliver in one hour')
    expect(draft.answers.categories).toEqual(['Shoes', 'Hats'])
    expect(draft.stagedUpload?.tenant_id).toBe('acme')
    expect(filled).toContain('Menu (2 items from the document)')
  })

  it('never overwrites values that are already filled in', () => {
    const base: WizardDraft = {
      ...emptyDraft(),
      tenantId: 'acme',
      vertical: 'ecommerce',
      supportEmail: 'typed@acme.example',
      openTime: '08:00',
      tone: 'warm and helpful', // admin picked a non-default tone
      neverState: 'My own rule',
    }
    const { draft } = applyStagedToDraft(base, STAGED)
    expect(draft.supportEmail).toBe('typed@acme.example')
    expect(draft.salesEmail).toBe('pdf@acme.example') // was empty — fills
    expect(draft.openTime).toBe('08:00')
    expect(draft.tone).toBe('warm and helpful')
    expect(draft.neverState).toBe('My own rule')
  })

  it('puts the document menu into the draft snapshot as buttons', () => {
    const base: WizardDraft = {
      ...emptyDraft(),
      tenantId: 'acme',
      vertical: 'ecommerce',
      companyName: 'Acme Retail',
    }
    const { draft } = applyStagedToDraft(base, STAGED)
    const snapshot = draftToSnapshot(draft)
    expect(snapshot.menu?.header).toBe('Acme Retail')
    expect(snapshot.menu?.buttons?.map((b) => b.id)).toEqual(['services', 'contact'])
    expect(snapshot.menu?.buttons?.[0].title).toBe('Our Services')
  })

  it('universal mode fills only document-derived fields (contacts yes; hours/tone/menu no)', () => {
    const base: WizardDraft = { ...emptyDraft(), tenantId: 'acme', vertical: 'ecommerce' }
    const { draft, filled, mode } = applyStagedToDraft(base, STAGED_UNIVERSAL)
    expect(mode).toBe('universal')
    // Brand and contacts are extracted from the document text — they fill.
    expect(draft.companyName).toBe('Acme Retail')
    expect(draft.supportEmail).toBe('pdf@acme.example')
    expect(draft.supportPhone).toBe('+91 11111 11111')
    expect(draft.answers.categories).toEqual(['Shoes', 'Hats'])
    // Hours, tone and guardrails may be template defaults in this payload —
    // they must NOT be dressed up as the company's own details.
    expect(draft.openTime).toBe(base.openTime)
    expect(draft.closeTime).toBe(base.closeTime)
    expect(draft.openDays).toEqual(base.openDays)
    expect(draft.tone).toBe(base.tone)
    expect(draft.neverState).toBe('')
    expect(filled.join(' ')).not.toContain('Menu (')
    expect(filled.join(' ')).not.toContain('Tone')
  })

  it('universal mode does not put the staged menu into the draft snapshot', () => {
    const base: WizardDraft = {
      ...emptyDraft(),
      tenantId: 'acme',
      vertical: 'ecommerce',
      companyName: 'Acme Retail',
    }
    const { draft } = applyStagedToDraft(base, STAGED_UNIVERSAL)
    const snapshot = draftToSnapshot(draft)
    const ids = snapshot.menu?.buttons?.map((b) => b.id) ?? []
    expect(ids).not.toContain('services')
    expect(ids).not.toContain('contact')
  })
})

describe('register wizard PDF auto-fill (admin, awaiting approval)', () => {
  it('stages the document, shows what was fetched, and protects existing values', async () => {
    testState.respond('POST /api/admin/tenants/acme/onboarding/stage', () => ({ body: STAGED }))
    await renderLockedAdminWizard()
    const user = await pickTenantAndUpload()

    // The summary names what the document filled in.
    expect(await screen.findByText(/Fetched from your PDF/)).toBeInTheDocument()
    const summary = await screen.findByText(/Filled in:/)
    expect(summary.textContent).toContain('Tone')
    expect(summary.textContent).toContain('Working days')
    expect(summary.textContent).toContain('Services list')
    expect(summary.textContent).toContain('Menu (2 items from the document)')

    // The stage call really went to the tenant-scoped endpoint.
    expect(
      testState.calls.some(
        (c) => c.method === 'POST' && c.path === '/api/admin/tenants/acme/onboarding/stage',
      ),
    ).toBe(true)

    // Existing values win: the resolved profile already had a support
    // email, so the document's pdf@ address must not replace it.
    await user.click(screen.getByRole('button', { name: /Continue/ }))
    const phone = await screen.findByLabelText(/WhatsApp phone number/)
    await user.clear(phone)
    await user.type(phone, '+91 98765 43210')
    await user.click(screen.getByRole('button', { name: /Continue/ }))
    const supportEmail = (await screen.findByLabelText(/Support email/)) as HTMLInputElement
    await waitFor(() => expect(supportEmail.value).toBe('support@acme.example'))
    const botName = (await screen.findByLabelText(/Bot name/)) as HTMLInputElement
    expect(botName.value).toBe('Acme Bot')
  })

  it('universal extraction gets the conservative summary (no PDF claims for template-risky fields)', async () => {
    testState.respond('POST /api/admin/tenants/acme/onboarding/stage', () => ({
      body: STAGED_UNIVERSAL,
    }))
    await renderLockedAdminWizard()
    await pickTenantAndUpload()

    expect(await screen.findByText(/Started from your document/)).toBeInTheDocument()
    expect(screen.queryByText(/Fetched from your PDF/)).not.toBeInTheDocument()
    const summary = await screen.findByText(/Filled in from your document:/)
    expect(summary.textContent).toContain('Services list')
    expect(summary.textContent).not.toContain('Menu (')
    expect(summary.textContent).not.toContain('Working days')
    expect(summary.textContent).toMatch(/did not confirm those/)
  })

  it('shows a clear error and stays fully usable when staging fails', async () => {
    testState.respond('POST /api/admin/tenants/acme/onboarding/stage', () => ({
      status: 500,
      body: { detail: 'Document parsing failed: boom' },
    }))
    await renderLockedAdminWizard()
    const user = await pickTenantAndUpload()

    expect(await screen.findByText(/could not read that document/i)).toBeInTheDocument()
    expect(screen.getByText(/complete every step by hand/)).toBeInTheDocument()
    // The wizard itself is untouched: the tenant is still picked and the
    // admin can carry on manually.
    expect(screen.getByRole('button', { name: /Continue/ })).toBeEnabled()
    expect(
      testState.calls.some(
        (c) => c.method === 'POST' && c.path === '/api/admin/tenants/acme/onboarding/stage',
      ),
    ).toBe(true)
    void user
  })
})
