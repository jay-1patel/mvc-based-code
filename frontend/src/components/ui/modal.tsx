import { useEffect, type ReactNode } from 'react'

/**
 * The shared modal shell: dimmed backdrop + centred panel.
 *
 * Every modal closes the same two ways unless it opts out — clicking the
 * backdrop, and pressing Escape. A modal marked `persistent` (e.g. one
 * showing an in-flight publish) ignores both; it can still be closed
 * through its own explicit controls.
 */
export function Modal({
  onClose,
  persistent = false,
  className,
  children,
}: {
  onClose: () => void
  persistent?: boolean
  /** Classes for the panel box itself (size, surface, layout). */
  className?: string
  children: ReactNode
}) {
  useEffect(() => {
    if (persistent) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, persistent])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm"
      onClick={
        persistent
          ? undefined
          : (event) => {
              if (event.target === event.currentTarget) onClose()
            }
      }
    >
      <div role="dialog" aria-modal="true" className={className}>
        {children}
      </div>
    </div>
  )
}
