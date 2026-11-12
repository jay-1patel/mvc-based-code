import { useState } from 'react'
import { KeyRound, Loader2, LogIn, ShieldCheck, UserPlus } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { ApiError } from '@/lib/api'
import { FieldInput, Notice, PrimaryButton } from '@/components/remake/ui'
import { PASSWORD_HINT, passwordError } from '@/lib/password'

export function LoginView() {
  const { needsSetup, login, setup } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (needsSetup) {
      const problem = passwordError(password)
      if (problem) {
        setError(problem)
        return
      }
    }
    setBusy(true)
    setError(null)
    try {
      if (needsSetup) await setup(username, password, email)
      else await login(username, password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-paper px-6 py-16 text-tx">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accsoft ring-1 ring-inset ring-line">
            <ShieldCheck className="h-6 w-6 text-acc" />
          </div>
          <h1 className="text-lg font-semibold text-tx">Tenant Console</h1>
          <p className="mt-1.5 text-xs leading-relaxed text-txmuted">
            {needsSetup
              ? 'No admin exists yet. Create the first super admin to start registering tenants.'
              : 'Sign in to manage your WhatsApp bot tenants.'}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-panel border border-line bg-card p-6">
          <FieldInput
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
          <FieldInput
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={needsSetup ? 'new-password' : 'current-password'}
            hint={needsSetup ? PASSWORD_HINT : undefined}
            required
          />
          {needsSetup && (
            <FieldInput
              label="Email (optional)"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          )}

          {error && <Notice tone="bad">{error}</Notice>}

          <PrimaryButton type="submit" className="w-full" disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : needsSetup ? (
              <UserPlus className="h-4 w-4" />
            ) : (
              <LogIn className="h-4 w-4" />
            )}
            {needsSetup ? 'Create super admin' : 'Sign in'}
          </PrimaryButton>
        </form>

        <p className="mt-6 flex items-center justify-center gap-2 text-xs text-txfaint">
          <KeyRound className="h-3 w-3" />
          Sessions are stored locally in this browser only.
        </p>
      </div>
    </div>
  )
}
