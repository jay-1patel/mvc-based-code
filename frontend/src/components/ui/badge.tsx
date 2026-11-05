import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'muted'

const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-panel text-slate-300 ring-surface-line',
  accent: 'bg-accent-50 text-accent-800 ring-accent-200',
  success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200',
  danger: 'bg-rose-50 text-rose-800 ring-rose-200',
  muted: 'bg-surface text-slate-500 ring-surface-line',
}

export function Badge({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: Tone
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function StatusDot({ tone }: { tone: Tone }) {
  const colour: Record<Tone, string> = {
    neutral: 'bg-slate-400',
    accent: 'bg-accent-500',
    success: 'bg-emerald-500',
    warning: 'bg-amber-500',
    danger: 'bg-rose-500',
    muted: 'bg-slate-500',
  }
  return <span className={cn('h-1.5 w-1.5 rounded-full', colour[tone])} />
}
