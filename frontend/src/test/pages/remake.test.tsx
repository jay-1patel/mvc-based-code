import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { renderApp, waitForScreen } from '../helpers'
import { SourceChip } from '@/components/remake/ui'
import { menuButtonSource } from '@/lib/source'
import { buildTenantNav } from '@/lib/navigation'
import type { LayerDetail, MenuButton } from '@/lib/types'

const button = (id: string): MenuButton => ({ id, title: id, sort_order: 1, enabled: true })

function detailWith(fileIds: string[], dbIds: string[]): LayerDetail {
  return {
    tenant_id: 'acme',
    layers: {
      file: { menu: { buttons: fileIds.map(button) } },
      db: { menu: { buttons: dbIds.map(button) } },
      resolved: {},
    },
  } as unknown as LayerDetail
}

describe('remake — source derivation', () => {
  it('maps layer membership to pdf / manual / default', () => {
    const detail = detailWith(['from_pdf'], ['typed_by_admin'])
    expect(menuButtonSource(button('from_pdf'), detail)).toBe('pdf')
    expect(menuButtonSource(button('typed_by_admin'), detail)).toBe('manual')
    expect(menuButtonSource(button('anything_else'), detail)).toBe('default')
    // An admin edit wins over the file copy of the same button.
    const both = detailWith(['shared'], ['shared'])
    expect(menuButtonSource(button('shared'), both)).toBe('manual')
    // No detail yet: nothing can be proven, so it is the default.
    expect(menuButtonSource(button('from_pdf'), null)).toBe('default')
  })

  it('SourceChip renders the four plain labels', () => {
    const { rerender } = render(<SourceChip source="pdf" />)
    expect(screen.getByText('From PDF')).toBeInTheDocument()
    rerender(<SourceChip source="web" />)
    expect(screen.getByText('From website')).toBeInTheDocument()
    rerender(<SourceChip source="manual" />)
    expect(screen.getByText('Typed by admin')).toBeInTheDocument()
    rerender(<SourceChip source="default" />)
    expect(screen.getByText('System default')).toBeInTheDocument()
  })
})

describe('remake — grouped navigation', () => {
  it('groups the tenant nav in the design order with plain names', () => {
    const nav = buildTenantNav({
      tenant: {
        id: 'acme',
        slug: 'acme',
        display_name: 'Acme Retail',
        vertical: 'ecommerce',
        status: 'active',
        current_version: 3,
        waba_phone_id: '123',
      },
      features: new Set(['orders', 'campaigns']),
      can: () => true,
    })
    const groups = [...new Set(nav.map((i) => i.group))]
    expect(groups).toEqual([
      'Today',
      'Talk to customers',
      'Your business data',
      'Grow',
      'Manage',
      'Settings → Advanced',
    ])
    const labels = nav.map((i) => i.label)
    expect(labels).toContain('Chats')
    expect(labels).toContain('Bot menu')
    expect(labels).toContain('Connections')
    expect(labels).toContain('Questions the bot understands')
    expect(labels).not.toContain('Live inbox')
    expect(labels).not.toContain('API tokens')
  })

  it('keeps permission gating: a principal without view_inbox loses Chats', () => {
    const nav = buildTenantNav({
      tenant: {
        id: 'acme',
        slug: 'acme',
        display_name: 'Acme Retail',
        vertical: 'ecommerce',
        status: 'active',
        current_version: 3,
        waba_phone_id: '123',
      },
      features: new Set(),
      can: (perm) => perm !== 'view_inbox',
    })
    expect(nav.map((i) => i.label)).not.toContain('Chats')
    expect(nav.map((i) => i.label)).toContain('Today')
  })
})

describe('remake — shell in the app', () => {
  it('renders the grouped nav and the super-admin platform group', async () => {
    renderApp('#/tenants/acme/overview')
    await waitForScreen()
    for (const group of ['Talk to customers', 'Your business data', 'Grow', 'Manage']) {
      expect((await screen.findAllByText(group)).length).toBeGreaterThan(0)
    }
    expect((await screen.findAllByText('Chats')).length).toBeGreaterThan(0)
    expect(await screen.findByRole('button', { name: /businesses/i })).toBeInTheDocument()
    // Super admin inside a tenant: the view-only banner keeps its wording.
    expect(await screen.findByText(/View-only — changes come from the tenant's admin/)).toBeInTheDocument()
    // Admin-only entries stay out of the super shell.
    expect(screen.queryByRole('button', { name: /my change requests/i })).not.toBeInTheDocument()
  })

  it('today shows Needs you before Today so far, with the waiting chat first', async () => {
    renderApp('#/tenants/acme/overview')
    await waitForScreen()
    const needs = await screen.findByText('Needs you')
    const soFar = await screen.findByText('Today so far')
    expect(
      needs.compareDocumentPosition(soFar) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(await screen.findByText(/chat is waiting for a human reply/)).toBeInTheDocument()
  })

  it('bot menu rows carry a source chip and the crawl caption', async () => {
    renderApp('#/tenants/acme/menu')
    await waitForScreen()
    expect((await screen.findAllByText('Help')).length).toBeGreaterThan(0)
    // The fixture tenant has no layer rows, so nothing can be proven
    // about the button's origin — it must say System default.
    expect((await screen.findAllByText('System default')).length).toBeGreaterThan(0)
    expect(
      await screen.findByText(/never from a website crawl/),
    ).toBeInTheDocument()
  })

  it('chats lists the waiting conversation first with its badge', async () => {
    renderApp('#/tenants/acme/inbox')
    await waitForScreen()
    expect(await screen.findByText('Priya')).toBeInTheDocument()
    expect((await screen.findAllByText(/waiting/i)).length).toBeGreaterThan(0)
  })
})
