import { useState } from 'react'
import { KeyRound, LogIn, ShieldCheck, UserPlus } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert } from '@/components/ui/feedback'

export function LoginView() {
  const { needsSetup, login, setup } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
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
    <div className="flex min-h-full items-center justify-center px-6 py-16">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accent-500/15 ring-1 ring-inset ring-accent-200">
            <ShieldCheck className="h-6 w-6 text-accent-700" />
          </div>
          <h1 className="text-lg font-semibold text-slate-100">Tenant Console</h1>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
            {needsSetup
              ? 'No admin exists yet. Create the first super admin to start registering tenants.'
              : 'Sign in to manage your WhatsApp bot tenants.'}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-xl bg-surface-raised p-6 ring-1 ring-surface-line">
          <Input
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={needsSetup ? 'new-password' : 'current-password'}
            hint={needsSetup ? 'At least 12 characters, not a common password.' : undefined}
            required
          />
          {needsSetup && (
            <Input
              label="Email (optional)"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          )}

          {error && <Alert tone="danger">{error}</Alert>}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={busy}
            icon={needsSetup ? <UserPlus className="h-4 w-4" /> : <LogIn className="h-4 w-4" />}
          >
            {needsSetup ? 'Create super admin' : 'Sign in'}
          </Button>
        </form>

        <p className="mt-6 flex items-center justify-center gap-2 text-xs text-slate-600">
          <KeyRound className="h-3 w-3" />
          Sessions are stored locally in this browser only.
        </p>
      </div>
    </div>
  )
}
