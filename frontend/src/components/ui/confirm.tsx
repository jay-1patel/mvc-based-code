import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/modal'

export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  /** Style the confirm button as destructive (deletes, irreversible sends). */
  danger?: boolean
}

type AskFn = (options: ConfirmOptions | string) => Promise<boolean>

const ConfirmContext = createContext<AskFn | null>(null)

/**
 * In-app replacement for the browser's native confirmation popup.
 * Mount once (next to ToastProvider); callers use `useConfirm()`.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((value: boolean) => void) | null>(null)

  const settle = useCallback((value: boolean) => {
    resolver.current?.(value)
    resolver.current = null
    setPending(null)
  }, [])

  const ask = useCallback<AskFn>(
    (options) => {
      // A second request supersedes one still open: settle it as cancelled.
      resolver.current?.(false)
      setPending(typeof options === 'string' ? { message: options } : options)
      return new Promise<boolean>((resolve) => {
        resolver.current = resolve
      })
    },
    [],
  )

  const value = useMemo(() => ask, [ask])

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {pending && (
        <Modal
          onClose={() => settle(false)}
          className="w-full max-w-md rounded-xl border border-surface-line bg-surface-card p-6 shadow-2xl"
        >
          {pending.title && (
            <h3 className="text-base font-semibold text-surface-fg">{pending.title}</h3>
          )}
          <p className="text-sm leading-relaxed text-slate-300">{pending.message}</p>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => settle(false)} autoFocus>
              {pending.cancelLabel ?? 'Cancel'}
            </Button>
            <Button
              variant={pending.danger ? 'danger' : 'primary'}
              onClick={() => settle(true)}
            >
              {pending.confirmLabel ?? 'Confirm'}
            </Button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  )
}

/**
 * Returns an async confirmation function: `await ask('Delete this?')`
 * resolves true when the user confirms, false on cancel / backdrop / Escape.
 */
export function useConfirm(): AskFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used inside <ConfirmProvider>')
  return ctx
}
