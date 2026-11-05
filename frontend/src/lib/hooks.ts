import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
  setData: (value: T | null) => void
}

/**
 * Minimal request-on-mount hook: fetch, abort on unmount, expose the error text
 * the server sent. Deliberately tiny — the app has a dozen endpoints, not a
 * thousand, so a cache library would be more machinery than the problem needs.
 */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    setLoading(true)
    fnRef
      .current(controller.signal)
      .then((value) => {
        if (cancelled) return
        setData(value)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled || (err as Error)?.name === 'AbortError') return
        setError(err instanceof ApiError ? err.message : ((err as Error)?.message ?? 'Request failed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading, reload, setData }
}

/** Track an async action (save, publish, rollback) with its own error slot. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const run = useCallback(async <T,>(fn: () => Promise<T>, successMessage?: string): Promise<T | null> => {
    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const result = await fn()
      if (successMessage) setSuccess(successMessage)
      return result
    } catch (err) {
      setError(err instanceof ApiError ? err.message : ((err as Error)?.message ?? 'Action failed'))
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, error, success, setError, setSuccess, run }
}
