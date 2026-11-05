import { useState, type KeyboardEvent } from 'react'
import { Plus, X } from 'lucide-react'
import { cn } from '@/lib/cn'

/**
 * Chips input: type and press Enter, or click a suggestion. Used wherever the
 * profile wants a list of domain words (technologies, categories, destinations)
 * that the bot should understand.
 */
export function TagInput({
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
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg bg-surface-raised p-2 ring-1 ring-inset ring-surface-line focus-within:ring-2 focus-within:ring-accent-500">
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-md bg-accent-500/15 px-2 py-1 text-xs text-accent-800 ring-1 ring-inset ring-accent-200"
          >
            {tag}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange(value.filter((v) => v !== tag))}
              className="text-accent-700 hover:text-accent-900 disabled:cursor-not-allowed disabled:opacity-40"
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
          className="min-w-32 flex-1 bg-transparent px-1 py-1 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none disabled:cursor-not-allowed"
        />
      </div>
      {unused.length > 0 && !disabled && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {unused.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-400',
                'ring-1 ring-inset ring-surface-line transition hover:bg-accent-50 hover:text-slate-100',
              )}
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
