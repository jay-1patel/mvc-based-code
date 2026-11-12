import type { Config } from 'tailwindcss'

/**
 * Remake semantic colours. Every value is a CSS variable from
 * `src/theme/tokens.css` — the ONLY place colour literals live. These
 * names are deliberately unlike the legacy palette below (`slate`,
 * `surface`, `accent`) so the token test can scan for exactly this set
 * and fail the build if one is ever used without a definition.
 */
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`
const semantic = {
  paper: v('paper'),
  card: v('card'),
  sidebg: v('sidebg'),
  inputbg: v('input-bg'),
  tx: v('tx'),
  txmuted: v('tx-muted'),
  txfaint: v('tx-faint'),
  line: v('line'),
  linesoft: v('line-soft'),
  acc: v('acc'),
  accsoft: v('acc-soft'),
  onacc: v('on-acc'),
  attbg: v('att-bg'),
  attline: v('att-line'),
  attink: v('att-ink'),
  badbg: v('bad-bg'),
  badline: v('bad-line'),
  badink: v('bad-ink'),
  ok: v('ok'),
  oksoft: v('ok-soft'),
  chippdfbg: v('chip-pdf-bg'),
  chippdfink: v('chip-pdf-ink'),
  chipwebbg: v('chip-web-bg'),
  chipwebink: v('chip-web-ink'),
  chipmanbg: v('chip-man-bg'),
  chipmanink: v('chip-man-ink'),
  chipdefbg: v('chip-def-bg'),
  chipdefink: v('chip-def-ink'),
  bubblein: v('bubble-in'),
  switchoff: v('switch-off'),
}

/**
 * Light theme: a light grey canvas with a purple cast, violet accents.
 *
 * Note the inverted `slate` scale: the numbers run dark → light instead of
 * light → dark, because the app was authored as "slate-100 is the strongest
 * text, slate-600 is the faintest hint". Flipping the scale keeps every one of
 * those usages correct without touching the markup. The hues themselves are
 * nudged towards purple so nothing reads as pure black, pure white or blue-grey.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        ...semantic,
        slate: {
          100: '#1a1626', // strongest text — deep plum-black, never #000
          200: '#28213a', // headings
          300: '#3b3350', // secondary text
          400: '#564c6d', // body-muted
          500: '#776d8c', // hints
          600: '#9c94ad', // faint
          700: '#c9c3d6', // dividers
        },
        surface: {
          DEFAULT: '#f1f0f7', // page — light grey, purple cast
          raised: '#fbfaff', // cards, inputs — near-white lavender
          overlay: '#fbfaff', // menus, popovers
          panel: '#f7f6fc', // sidebar, sunken blocks
          line: '#e2dfef', // borders
          // Aliases the markup was authored against (onboarding components
          // especially). Text steps come from the ink scale, background
          // steps from the surface scale above — same hues, no new colours.
          fg: '#1a1626', // primary text on a surface (= ink / slate-100)
          muted: '#564c6d', // secondary text on a surface (= ink-soft / slate-400)
          bg: '#f1f0f7', // page canvas (= DEFAULT)
          base: '#f7f6fc', // sunken blocks, banner bodies (= panel)
          card: '#fbfaff', // card bodies, dropdowns (= raised)
          elevated: '#f6f3ff', // hover step above a card (= accent-50)
        },
        ink: {
          DEFAULT: '#1a1626',
          soft: '#564c6d',
          faint: '#9c94ad',
        },
        accent: {
          50: '#f6f3ff',
          100: '#ede8fe',
          200: '#ddd3fd',
          300: '#c5b2fb',
          400: '#a983f7',
          500: '#8d5cf0',
          600: '#7c3aed',
          700: '#6b21d9',
          800: '#5a1bb4',
          900: '#4a1a8f',
        },
      },
      borderRadius: {
        panel: '14px',
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      // Bigger type across the board: 17px root, so every rem-based Tailwind
      // step lands roughly 6% larger than the stock scale.
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        xs: ['0.8125rem', { lineHeight: '1.3rem' }],
        sm: ['0.9375rem', { lineHeight: '1.45rem' }],
        base: ['1.0625rem', { lineHeight: '1.65rem' }],
        lg: ['1.1875rem', { lineHeight: '1.8rem' }],
        xl: ['1.375rem', { lineHeight: '1.9rem' }],
        '2xl': ['1.625rem', { lineHeight: '2.1rem' }],
        '3xl': ['1.9375rem', { lineHeight: '2.3rem' }],
      },
      // Half steps the default scale skips. h-4.5 is the small Switch track
      // (Capabilities flags, permission matrix) and h-10.5 is the medium Button
      // height; without these the classes generate nothing and the elements
      // collapse.
      spacing: {
        '4.5': '1.125rem',
        '10.5': '2.625rem',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 160ms ease-out',
      },
    },
  },
  plugins: [],
} satisfies Config
