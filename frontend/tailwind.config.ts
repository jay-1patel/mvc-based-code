import type { Config } from 'tailwindcss'

/**
 * Light canvas, deep-ink sidebar, teal accent.
 *
 * Note the inverted `slate` scale: the numbers run dark → light instead of
 * light → dark, because the app was authored as "slate-100 is the strongest
 * text, slate-600 is the faintest hint". Flipping the scale keeps every one of
 * those usages correct without touching the markup.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        slate: {
          100: '#14221c',
          200: '#1c3329',
          300: '#2c463b',
          400: '#4d6559',
          500: '#6d8378',
          600: '#96a89f',
          700: '#c9d5cf',
        },
        surface: {
          DEFAULT: '#f4f1ea',
          raised: '#fffcf7',
          overlay: '#fffcf7',
          panel: '#f7f4ee',
          line: '#e4ddd2',
          fg: '#14221c',
          muted: '#4d6559',
          bg: '#f4f1ea',
          base: '#f7f4ee',
          card: '#fffcf7',
          elevated: '#eef6f3',
        },
        ink: {
          DEFAULT: '#14221c',
          soft: '#4d6559',
          faint: '#96a89f',
        },
        accent: {
          50: '#eef6f3',
          100: '#d7eee6',
          200: '#b3ddd0',
          300: '#7ec4ae',
          400: '#45a58a',
          500: '#1f8a6e',
          600: '#0f6e58',
          700: '#0c5746',
          800: '#0d4639',
          900: '#0c3a30',
        },
      },
      fontFamily: {
        sans: ['"Source Sans 3"', 'Segoe UI', 'system-ui', 'sans-serif'],
        display: ['Outfit', '"Source Sans 3"', 'system-ui', 'sans-serif'],
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
