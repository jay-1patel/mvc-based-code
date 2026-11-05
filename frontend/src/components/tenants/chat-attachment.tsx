import { FileText, Loader2, X } from 'lucide-react'
import type { Attachment } from '@/lib/attachments'
import { formatBytes } from '@/lib/uploads'
import { cn } from '@/lib/cn'

/** Render an attachment inside a chat bubble: image preview, or a file link. */
export function AttachmentBubble({ attachment, mine }: { attachment: Attachment; mine?: boolean }) {
  if (attachment.type === 'image') {
    return (
      <a href={attachment.url} target="_blank" rel="noopener noreferrer" className="block">
        <img
          src={attachment.url}
          alt={attachment.name}
          className="max-h-56 w-auto max-w-full rounded-lg object-cover"
        />
      </a>
    )
  }
  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        'flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm underline-offset-2 hover:underline',
        mine ? 'bg-white/15 text-white' : 'bg-accent-50 text-slate-200',
      )}
    >
      <FileText className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
      {typeof attachment.size === 'number' && (
        <span className="shrink-0 text-xs opacity-70">{formatBytes(attachment.size)}</span>
      )}
    </a>
  )
}

/** An upload in progress or waiting to be sent, shown beside the composer. */
export function AttachmentChip({
  attachment,
  uploading,
  onRemove,
}: {
  attachment: Attachment
  uploading?: boolean
  onRemove?: () => void
}) {
  return (
    <div className="inline-flex items-center gap-2 rounded-lg bg-surface-overlay px-2.5 py-1.5 text-sm text-slate-200 ring-1 ring-inset ring-surface-line">
      {uploading ? (
        <Loader2 className="h-4 w-4 animate-spin text-accent-400" />
      ) : (
        <FileText className="h-4 w-4 text-accent-400" />
      )}
      <span className="max-w-[14rem] truncate">{attachment.name}</span>
      {typeof attachment.size === 'number' && (
        <span className="text-xs text-slate-400">{formatBytes(attachment.size)}</span>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-slate-400 transition hover:text-slate-100"
          aria-label="Remove attachment"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  )
}
