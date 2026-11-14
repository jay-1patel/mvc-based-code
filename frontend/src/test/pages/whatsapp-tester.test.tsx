import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WhatsAppTester } from '@/components/tester/whatsapp-tester'
import { testSimulatorApi, type SimulatorResult } from '@/lib/test-simulator'
import { parseWhatsAppFormat } from '@/lib/whatsapp-format'

/**
 * WhatsApp replica console: the phone renders the backend's
 * delivery-shaped phone messages (whatsapp), buttons/list rows are
 * tappable and feed their payload back, and opening the page plays the
 * new-customer flow (reset + "Hi").
 */

vi.mock('@/lib/test-simulator', () => ({
  testSimulatorApi: {
    send: vi.fn(),
    reset: vi.fn(),
  },
}))

const sendMock = vi.mocked(testSimulatorApi.send)
const resetMock = vi.mocked(testSimulatorApi.reset)

const OPENING: SimulatorResult = {
  status: 'processed',
  new_state: 'B2C_MAIN_MENU',
  user_type: 'b2c',
  human_handover: false,
  messages: [
    { type: 'list', header: 'Probe Store', body: 'What would you like to explore today?' },
    { type: 'buttons', text: 'Pick a shortcut', buttons: [{ id: 'opt_a', title: 'Alpha' }] },
  ],
  whatsapp: [
    {
      kind: 'list',
      header: 'Probe Store',
      body: 'What would you like to explore today?',
      button_text: 'Show Options',
      sections: [
        {
          title: 'Options',
          rows: [
            { id: 'menu_products', title: 'View Products', description: 'Browse all products' },
            { id: 'menu_about', title: 'About Us', description: '' },
          ],
        },
      ],
      source_index: 0,
    },
    {
      kind: 'buttons',
      text: 'Pick a shortcut',
      buttons: [{ id: 'opt_a', title: 'Alpha' }],
      source_index: 1,
    },
  ],
}

const FOLLOWUP: SimulatorResult = {
  status: 'processed',
  new_state: 'B2C_MAIN_MENU',
  user_type: 'b2c',
  messages: [{ type: 'text', text: 'Here is the *answer* you asked for.' }],
  whatsapp: [{ kind: 'text', text: 'Here is the *answer* you asked for.', source_index: 0 }],
}

beforeEach(() => {
  vi.clearAllMocks()
  resetMock.mockResolvedValue({
    wa_id: '919876543210',
    reset: true,
    new_state: null,
    human_handover: false,
    status: 'reset',
  })
  sendMock.mockImplementation(async (input) =>
    input.message === 'Hi' ? OPENING : FOLLOWUP,
  )
})

describe('WhatsApp replica console', () => {
  it('plays the new-customer opening on open: reset, then Hi, menu rendered as WhatsApp renders it', async () => {
    render(<WhatsAppTester tenantId="acme" />)

    expect(await screen.findByText('What would you like to explore today?')).toBeTruthy()
    expect(resetMock).toHaveBeenCalledTimes(1)
    expect(sendMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'Hi', tenant_id: 'acme' }),
    )
    // List message shows its button; quick-reply button is attached.
    expect(screen.getByRole('button', { name: /Show Options/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Alpha' })).toBeTruthy()
    // The user's own "Hi" bubble is visible, as on the phone.
    expect(screen.getByText('Hi')).toBeTruthy()
  })

  it('opens the list sheet with sections and rows; tapping a row sends its id and shows its title', async () => {
    const user = userEvent.setup()
    render(<WhatsAppTester tenantId="acme" />)
    await screen.findByText('What would you like to explore today?')

    await user.click(screen.getByRole('button', { name: /Show Options/ }))
    expect(await screen.findByText('View Products')).toBeTruthy()
    expect(screen.getByText('Browse all products')).toBeTruthy()
    expect(screen.getByText('About Us')).toBeTruthy()

    await user.click(screen.getByText('View Products'))
    await waitFor(() =>
      expect(sendMock).toHaveBeenCalledWith(
        expect.objectContaining({ message: 'menu_products' }),
      ),
    )
    // The follow-up bot answer arrives (its bold word renders as <strong>).
    expect(await screen.findByText('answer')).toBeTruthy()
  })

  it('tapping a quick-reply button sends the button id', async () => {
    const user = userEvent.setup()
    render(<WhatsAppTester tenantId="acme" />)
    await screen.findByText('What would you like to explore today?')

    await user.click(screen.getByRole('button', { name: 'Alpha' }))
    await waitFor(() =>
      expect(sendMock).toHaveBeenCalledWith(expect.objectContaining({ message: 'opt_a' })),
    )
  })

  it('renders bot answers with WhatsApp formatting (bold becomes strong)', async () => {
    const user = userEvent.setup()
    render(<WhatsAppTester tenantId="acme" />)
    await screen.findByText('What would you like to explore today?')

    await user.click(screen.getByRole('button', { name: 'Alpha' }))
    const strong = await screen.findByText('answer')
    expect(strong.tagName).toBe('STRONG')
  })

  it('shows the raw captured payload on demand', async () => {
    const user = userEvent.setup()
    render(<WhatsAppTester tenantId="acme" />)
    await screen.findByText('What would you like to explore today?')

    const toggles = await screen.findAllByText('view raw payload')
    await user.click(toggles[0])
    expect(await screen.findByText(/"type": "list"/)).toBeTruthy()
  })

  it('sends free text typed in the composer', async () => {
    const user = userEvent.setup()
    render(<WhatsAppTester tenantId="acme" />)
    await screen.findByText('What would you like to explore today?')

    const box = screen.getByPlaceholderText('Type a message')
    await user.type(box, 'What is the price?')
    await user.click(screen.getByRole('button', { name: /Send/ }))
    await waitFor(() =>
      expect(sendMock).toHaveBeenCalledWith(
        expect.objectContaining({ message: 'What is the price?' }),
      ),
    )
    expect(await screen.findByText('What is the price?')).toBeTruthy()
  })
})

describe('parseWhatsAppFormat', () => {
  it('parses bold, italic, strike and mono', () => {
    const segs = parseWhatsAppFormat('a *bold* _it_ ~gone~ `code` z')
    expect(segs).toEqual([
      { text: 'a ' },
      { text: 'bold', bold: true },
      { text: ' ' },
      { text: 'it', italic: true },
      { text: ' ' },
      { text: 'gone', strike: true },
      { text: ' ' },
      { text: 'code', mono: true },
      { text: ' z' },
    ])
  })

  it('leaves stray asterisks literal (prices, multiplication)', () => {
    const segs = parseWhatsAppFormat('Rs 5 * 2 = 10 and 3*4')
    expect(segs.map((s) => s.text).join('')).toBe('Rs 5 * 2 = 10 and 3*4')
    expect(segs.some((s) => s.bold)).toBe(false)
  })
})
