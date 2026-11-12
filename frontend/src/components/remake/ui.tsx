import {
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  useState,
  type KeyboardEvent,
} from 'react'
import { Check as CheckIcon, ChevronDown, Copy, Plus, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { SOURCE_LABELS, type DataSource } from '@/lib/source'

/**
 * Shared building blocks for the remake screens.
 *
 * Every colour here is a semantic token (see `src/theme/tokens.css`); this
 * file — like every remake file — contains no colour literals, so a future
 * re-skin only ever edits the token file.
 */

export function Card({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('rounded-panel border border-line bg-card', className)}>
      {children}
    </div>
  )
}

export function Panel({
  title,
  children,
  className,
  action,
}: {
  title: string
  children: ReactNode
  className?: string
  action?: ReactNode
}) {
  return (
    <section className={cn('rounded-panel border border-line bg-card p-4 sm:p-5', className)}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-tx">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export type StatTone = 'normal' | 'attention' | 'failure'

export function StatCard({
  value,
  label,
  tone = 'normal',
}: {
  value: ReactNode
  label: string
  tone?: StatTone
}) {
  return (
    <div
      className={cn(
        'rounded-panel border p-4',
        tone === 'normal' && 'border-line bg-card',
        tone === 'attention' && 'border-attline bg-attbg',
        tone === 'failure' && 'border-badline bg-badbg',
      )}
    >
      <p
        className={cn(
          'text-2xl font-bold leading-none',
          tone === 'normal' && 'text-tx',
          tone === 'attention' && 'text-attink',
          tone === 'failure' && 'text-badink',
        )}
      >
        {value}
      </p>
      <p className={cn('mt-1.5 text-sm', tone === 'normal' ? 'text-txmuted' : 'text-tx')}>
        {label}
      </p>
    </div>
  )
}

const CHIP_CLASSES: Record<DataSource, string> = {
  pdf: 'bg-chippdfbg text-chippdfink',
  manual: 'bg-chipmanbg text-chipmanink',
  default: 'bg-chipdefbg text-chipdefink',
}

export function SourceChip({
  source,
  className,
}: {
  source: DataSource | 'web'
  className?: string
}) {
  if (source === 'web') {
    return (
      <span
        className={cn(
          'inline-block shrink-0 rounded-full bg-chipwebbg px-2 py-0.5 text-xs font-semibold text-chipwebink',
          className,
        )}
      >
        From website
      </span>
    )
  }
  return (
    <span
      className={cn(
        'inline-block shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold',
        CHIP_CLASSES[source],
        className,
      )}
    >
      {SOURCE_LABELS[source]}
    </span>
  )
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  className,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  className?: string
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg bg-acc px-3.5 py-2 text-sm font-semibold text-onacc transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
    >
      {children}
    </button>
  )
}

export function GhostButton({
  children,
  onClick,
  disabled,
  className,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  className?: string
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg border border-line bg-card px-3.5 py-2 text-sm font-medium text-tx transition hover:bg-paper disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
    >
      {children}
    </button>
  )
}

export type PillTone = 'neutral' | 'ok' | 'warn' | 'bad'

export function Pill({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: PillTone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-block rounded-full px-2.5 py-0.5 text-xs font-medium',
        tone === 'neutral' && 'bg-chipdefbg text-txmuted',
        tone === 'ok' && 'bg-oksoft text-ok',
        tone === 'warn' && 'bg-attbg text-attink',
        tone === 'bad' && 'bg-badbg text-badink',
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Nav badge: red for waiting-on-a-human counts, amber for error counts. */
export function CountBadge({
  count,
  tone = 'bad',
  className,
}: {
  count: number
  tone?: 'bad' | 'warn'
  className?: string
}) {
  if (!count) return null
  return (
    <span
      className={cn(
        'ml-auto shrink-0 rounded-full px-1.5 py-px text-xs font-bold',
        tone === 'bad' ? 'bg-badink text-onacc' : 'bg-attink text-onacc',
        className,
      )}
    >
      {count}
    </span>
  )
}

export function EmptyState({
  title,
  description,
  className,
  action,
}: {
  title: string
  description?: string
  className?: string
  action?: ReactNode
}) {
  return (
    <div className={cn('px-4 py-10 text-center', className)}>
      <p className="text-sm font-medium text-tx">{title}</p>
      {description && <p className="mx-auto mt-1 max-w-sm text-sm text-txmuted">{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  )
}

/**
 * An error that stays on screen, with the request id the console error
 * tracing already attaches to API failures — copyable, so a user can quote
 * it instead of describing a vanished toast.
 */
export function ErrorBanner({
  title = 'Something went wrong',
  message,
  requestId,
  className,
}: {
  title?: string
  message: string
  requestId?: string | null
  className?: string
}) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (!requestId) return
    try {
      await navigator.clipboard.writeText(requestId)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable — the id stays visible to copy by hand */
    }
  }
  return (
    <div role="alert" className={cn('rounded-panel border border-badline bg-badbg p-4', className)}>
      <p className="text-sm font-semibold text-badink">{title}</p>
      <p className="mt-1 text-sm text-tx">{message}</p>
      {requestId && (
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-txmuted">
          Error id: <code className="font-mono text-tx">{requestId}</code>
          <button
            type="button"
            onClick={copy}
            className="inline-flex items-center gap-1 rounded-md border border-badline bg-card px-2 py-1 font-medium text-tx transition hover:bg-badbg"
          >
            {copied ? <CheckIcon className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </p>
      )}
    </div>
  )
}

/** One row in a "Needs you" list: text on the left, a single action. */
export function NeedRow({
  icon,
  children,
  action,
}: {
  icon?: ReactNode
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex items-center gap-3 border-t border-linesoft py-2.5 first:border-t-0 first:pt-1 last:pb-0">
      {icon && <span className="shrink-0 text-txmuted">{icon}</span>}
      <div className="min-w-0 flex-1 text-sm text-tx">{children}</div>
      {action}
    </div>
  )
}

/** A destructive action button (delete, remove forever). */
export function DangerButton({
  children,
  onClick,
  disabled,
  className,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  className?: string
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg border border-badline bg-badbg px-3.5 py-2 text-sm font-semibold text-badink transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** A small icon-only ghost button (row actions: preview, download, delete). */
export function IconButton({
  children,
  onClick,
  disabled,
  title,
  danger = false,
  className,
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  title?: string
  danger?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'inline-flex items-center justify-center rounded-lg p-2 text-txmuted transition hover:bg-paper hover:text-tx disabled:cursor-not-allowed disabled:opacity-50',
        danger && 'text-badink hover:bg-badbg hover:text-badink',
        className,
      )}
    >
      {children}
    </button>
  )
}

/**
 * An inline notice strip for information, warnings and successes that stay
 * on screen (the danger counterpart is `ErrorBanner` above).
 */
export function Notice({
  tone = 'info',
  title,
  children,
  className,
}: {
  tone?: 'info' | 'warn' | 'success' | 'bad'
  title?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-panel border p-4',
        tone === 'info' && 'border-line bg-accsoft',
        tone === 'warn' && 'border-attline bg-attbg',
        tone === 'success' && 'border-line bg-oksoft',
        tone === 'bad' && 'border-badline bg-badbg',
        className,
      )}
    >
      {title && (
        <p
          className={cn(
            'text-sm font-semibold',
            tone === 'info' && 'text-acc',
            tone === 'warn' && 'text-attink',
            tone === 'success' && 'text-ok',
            tone === 'bad' && 'text-badink',
          )}
        >
          {title}
        </p>
      )}
      <div className={cn('text-sm text-tx', title && 'mt-1')}>{children}</div>
    </div>
  )
}

const fieldLabelClass = 'mb-1.5 block text-sm font-medium text-tx'
const fieldControlClass =
  'w-full rounded-lg border border-line bg-inputbg px-3 py-2 text-sm text-tx placeholder:text-txfaint transition focus:border-acc focus:outline-none disabled:cursor-not-allowed disabled:opacity-60'

function fieldNote(error?: string, hint?: ReactNode) {
  if (error) return <p className="mt-1 text-xs text-badink">{error}</p>
  if (hint) return <p className="mt-1 text-xs text-txfaint">{hint}</p>
  return null
}

export interface FieldInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  hint?: ReactNode
  error?: string
  prefix?: string
}

/**
 * Labelled text input on the token layer. Keeps the legacy console's id
 * scheme (`in-<label slug>`) so label-based lookups keep working.
 */
export function FieldInput({ label, hint, error, prefix, id, className, ...rest }: FieldInputProps) {
  const inputId = id ?? (label ? `in-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={inputId} className={fieldLabelClass}>
          {label}
        </label>
      )}
      <div className="relative">
        {prefix && (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-txmuted">
            {prefix}
          </span>
        )}
        <input
          id={inputId}
          className={cn(fieldControlClass, prefix && 'pl-9', error && 'border-badline', className)}
          {...rest}
        />
      </div>
      {fieldNote(error, hint)}
    </div>
  )
}

export interface FieldTextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  hint?: ReactNode
  error?: string
}

export function FieldTextarea({ label, hint, error, id, className, rows = 3, ...rest }: FieldTextareaProps) {
  const fieldId = id ?? (label ? `ta-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={fieldId} className={fieldLabelClass}>
          {label}
        </label>
      )}
      <textarea
        id={fieldId}
        rows={rows}
        className={cn(fieldControlClass, 'resize-y leading-relaxed', error && 'border-badline', className)}
        {...rest}
      />
      {fieldNote(error, hint)}
    </div>
  )
}

export interface FieldSelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  hint?: ReactNode
  error?: string
}

export function FieldSelect({ label, hint, error, id, className, children, ...rest }: FieldSelectProps) {
  const fieldId = id ?? (label ? `sel-${label.replace(/\W+/g, '-').toLowerCase()}` : undefined)
  return (
    <div>
      {label && (
        <label htmlFor={fieldId} className={fieldLabelClass}>
          {label}
        </label>
      )}
      <div className="relative">
        <select
          id={fieldId}
          className={cn(fieldControlClass, 'appearance-none pr-9', error && 'border-badline', className)}
          {...rest}
        >
          {children}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-txmuted" />
      </div>
      {fieldNote(error, hint)}
    </div>
  )
}

/** On/off switch on the token layer — same contract as the legacy Switch. */
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  size = 'md',
  ariaLabel,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label?: ReactNode
  description?: ReactNode
  disabled?: boolean
  size?: 'sm' | 'md'
  ariaLabel?: string
}) {
  const track = size === 'sm' ? 'h-4 w-7' : 'h-5 w-9'
  const knob = size === 'sm' ? 'h-3 w-3' : 'h-4 w-4'
  const shift = size === 'sm' ? 'translate-x-3' : 'translate-x-4'
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'group flex items-start gap-3 text-left disabled:cursor-not-allowed disabled:opacity-50',
        label ? 'w-full' : 'w-auto',
      )}
    >
      <span
        className={cn(
          'relative mt-0.5 shrink-0 rounded-full transition-colors',
          track,
          checked ? 'bg-acc' : 'bg-switchoff',
        )}
      >
        <span
          className={cn(
            'absolute left-0.5 top-1/2 -translate-y-1/2 rounded-full bg-card shadow-sm ring-1 ring-line transition-transform',
            knob,
            checked && shift,
          )}
        />
      </span>
      {label && (
        <span className="min-w-0">
          <span className="block text-sm text-tx">{label}</span>
          {description && <span className="mt-0.5 block text-xs leading-relaxed text-txmuted">{description}</span>}
        </span>
      )}
    </button>
  )
}

/** Checkbox with label and optional description — the legacy Checkbox contract. */
export function Check({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: ReactNode
  description?: ReactNode
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 text-sm">
      <span
        className={cn(
          'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition',
          checked ? 'border-acc bg-acc' : 'border-line bg-inputbg',
        )}
      >
        {checked && <CheckIcon className="h-3 w-3 text-onacc" strokeWidth={3.5} />}
      </span>
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0">
        <span className="block text-tx">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-txmuted">{description}</span>}
      </span>
    </label>
  )
}

/**
 * Chips input on the token layer — same behaviour as the legacy TagInput
 * (Enter or comma adds, Backspace removes the last, blur commits, optional
 * one-click suggestions).
 */
export function TagField({
  value,
  onChange,
  suggestions = [],
  placeholder = 'Add and press Enter',
  id,
  disabled = false,
}: {
  value: string[]
  onChange: (next: string[]) => void
  suggestions?: string[]
  placeholder?: string
  id?: string
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')

  const add = (raw: string) => {
    if (disabled) return
    const clean = raw.trim()
    if (!clean) return
    if (value.some((v) => v.toLowerCase() === clean.toLowerCase())) {
      setDraft('')
      return
    }
    onChange([...value, clean])
    setDraft('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      add(draft)
    }
    if (event.key === 'Backspace' && !draft && value.length) {
      onChange(value.slice(0, -1))
    }
  }

  const unused = suggestions.filter((s) => !value.includes(s))

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line bg-inputbg p-2 focus-within:border-acc">
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-md bg-accsoft px-2 py-1 text-xs font-medium text-acc"
          >
            {tag}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange(value.filter((v) => v !== tag))}
              className="text-acc transition hover:opacity-70 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => add(draft)}
          placeholder={value.length ? '' : placeholder}
          className="min-w-32 flex-1 bg-transparent px-1 py-1 text-sm text-tx placeholder:text-txfaint focus:outline-none disabled:cursor-not-allowed"
        />
      </div>
      {unused.length > 0 && !disabled && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {unused.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-txmuted transition hover:bg-paper hover:text-tx"
            >
              <Plus className="h-3 w-3" />
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
