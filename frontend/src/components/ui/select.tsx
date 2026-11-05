import { forwardRef, type SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/cn'

const base =
  'w-full appearance-none rounded-lg bg-surface-raised px-3 py-2 pr-9 text-sm text-slate-100 ' +
  'ring-1 ring-inset ring-surface-line transition focus:outline-none focus:ring-2 focus:ring-accent-500 ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  hint?: string
  error?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, label, hint, error, id, children, ...rest },
  ref,
) {
  const fieldId = id ?? (label ? `sel-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={fieldId} className="field-label">
          {label}
        </label>
      )}
      <div className="relative">
        <select ref={ref} id={fieldId} className={cn(base, className)} {...rest}>
          {children}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
      </div>
      {error ? <p className="hint text-rose-700">{error}</p> : hint ? <p className="hint">{hint}</p> : null}
    </div>
  )
})
