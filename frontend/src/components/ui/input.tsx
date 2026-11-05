import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

const base =
  'w-full rounded-lg bg-surface-raised px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 ' +
  'ring-1 ring-inset ring-surface-line transition focus:outline-none focus:ring-2 focus:ring-accent-500 ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  hint?: ReactNode
  error?: string
  prefix?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, label, hint, error, prefix, id, ...rest },
  ref,
) {
  const inputId = id ?? (label ? `in-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={inputId} className="field-label">
          {label}
        </label>
      )}
      <div className="relative">
        {prefix && (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">
            {prefix}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          className={cn(base, prefix && 'pl-9', error && 'ring-rose-500/70 focus:ring-rose-500', className)}
          {...rest}
        />
      </div>
      {error ? <p className="hint text-rose-700">{error}</p> : hint ? <p className="hint">{hint}</p> : null}
    </div>
  )
})

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  hint?: ReactNode
  error?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, label, hint, error, id, rows = 3, ...rest },
  ref,
) {
  const fieldId = id ?? (label ? `ta-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={fieldId} className="field-label">
          {label}
        </label>
      )}
      <textarea
        ref={ref}
        id={fieldId}
        rows={rows}
        className={cn(base, 'resize-y leading-relaxed', error && 'ring-rose-500/70', className)}
        {...rest}
      />
      {error ? <p className="hint text-rose-700">{error}</p> : hint ? <p className="hint">{hint}</p> : null}
    </div>
  )
})
