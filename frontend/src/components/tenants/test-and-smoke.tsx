import { useState } from 'react'
import { FlaskConical, Phone, Play, ShieldCheck } from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { tenantsApi, useTenants } from '@/lib/tenants'
import { useAuth } from '@/lib/auth'
import type { SmokeReport } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Alert, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'

const SAMPLES = [
  'hi',
  'I want to talk to a human',
  'what are your prices?',
  'I want a quote for a website',
  'track my order TG-1042',
  'I want to complain about my last purchase',
]

export function TestAndSmoke({ tenantId }: { tenantId: string }) {
  return (
    <div>
      <PageHeader
        title="Test and smoke"
        description="Run an utterance through this tenant's live intent matcher, and assert that the runtime really is data-derived. Both read the published profile — publish first if the answers look stale."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <TestQuestionCard tenantId={tenantId} />
        <div className="space-y-4">
          <SmokeCard tenantId={tenantId} />
          <PhoneBindingCard tenantId={tenantId} />
        </div>
      </div>
    </div>
  )
}

function TestQuestionCard({ tenantId }: { tenantId: string }) {
  const [message, setMessage] = useState('')
  const action = useAction()
  const [result, setResult] = useState<Awaited<ReturnType<typeof tenantsApi.testQuestion>> | null>(null)

  const run = async (text: string) => {
    const value = text.trim()
    if (!value) return
    setMessage(value)
    const res = await action.run(() => tenantsApi.testQuestion(tenantId, value))
    if (res) setResult(res)
  }

  return (
    <Card>
      <CardHeader
        title="Test a question"
        description="Rules first, embeddings second — the same two tiers the eval harness drives."
        icon={<FlaskConical className="h-4 w-4" />}
      />
      <CardBody className="space-y-4">
        <div className="flex gap-2">
          <Input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run(message)}
            placeholder="Type what a customer would say…"
          />
          <Button variant="primary" loading={action.busy} onClick={() => run(message)} icon={<Play className="h-4 w-4" />}>
            Run
          </Button>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {SAMPLES.map((sample) => (
            <button
              key={sample}
              type="button"
              onClick={() => run(sample)}
              className="rounded-md px-2 py-1 text-xs text-slate-400 ring-1 ring-inset ring-surface-line transition hover:bg-accent-50 hover:text-slate-100"
            >
              {sample}
            </button>
          ))}
        </div>

        {action.error && <Alert tone="danger">{action.error}</Alert>}

        {result && (
          <div className="space-y-3 rounded-lg bg-surface/60 p-4 ring-1 ring-inset ring-surface-line">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500">“{result.message}” →</span>
              {result.intent ? (
                <Badge tone="accent">{result.intent}</Badge>
              ) : (
                <Badge tone="warning">no intent matched</Badge>
              )}
              <Badge tone="neutral">score {result.score}</Badge>
              <Badge tone="neutral">tier: {result.tier}</Badge>
              {result.flow && <Badge tone="success">flow: {result.flow}</Badge>}
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">
                Routable intents ({result.active_intents.length})
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {result.active_intents.map((name) => (
                  <span
                    key={name}
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      name === result.intent
                        ? 'bg-accent-500/20 text-accent-800 ring-1 ring-inset ring-accent-300'
                        : 'bg-surface-panel text-slate-500'
                    }`}
                  >
                    {name}
                  </span>
                ))}
                {!result.active_intents.length && (
                  <span className="text-xs text-slate-500">none — this tenant cannot route anything</span>
                )}
              </div>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

function SmokeCard({ tenantId }: { tenantId: string }) {
  const action = useAction()
  const smoke = useAsync<SmokeReport>(() => tenantsApi.smoke(tenantId), [tenantId])

  return (
    <Card>
      <CardHeader
        title="Smoke test"
        description="Asserts menus and intents equal the profile, not a hardcoded constant."
        icon={<ShieldCheck className="h-4 w-4" />}
        actions={
          <Button size="sm" variant="secondary" loading={action.busy || smoke.loading} onClick={smoke.reload}>
            Run
          </Button>
        }
      />
      <CardBody className="space-y-3">
        {smoke.error && <Alert tone="danger">{smoke.error}</Alert>}
        {smoke.loading && !smoke.data ? (
          <LoadingBlock label="Running…" />
        ) : smoke.data ? (
          <>
            <div className="flex items-center gap-2">
              {smoke.data.ok ? (
                <Badge tone="success">passed</Badge>
              ) : (
                <Badge tone="danger">failed</Badge>
              )}
              <span className="text-xs text-slate-500">
                {smoke.data.vertical} · v{smoke.data.version}
              </span>
            </div>
            {smoke.data.forbidden_term_leaks.length > 0 && (
              <Alert tone="danger" title="Forbidden terms leaked into the profile copy">
                {smoke.data.forbidden_term_leaks.join(', ')}
              </Alert>
            )}
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">
                Visible buttons ({smoke.data.visible_buttons.length})
              </p>
              <p className="mt-1 font-mono text-xs text-slate-400">
                {smoke.data.visible_buttons.join(' · ') || 'none'}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Intent gates</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {Object.entries(smoke.data.gated_intents).map(([intent, on]) => (
                  <Badge key={intent} tone={on ? 'success' : 'muted'}>
                    {intent}: {on ? 'open' : 'closed'}
                  </Badge>
                ))}
              </div>
            </div>
          </>
        ) : (
          <p className="text-xs text-slate-500">Run the smoke test to check this tenant.</p>
        )}
      </CardBody>
    </Card>
  )
}

function PhoneBindingCard({ tenantId }: { tenantId: string }) {
  const { tenants, reload } = useTenants()
  const { can } = useAuth()
  const toast = useToast()
  const action = useAction()
  const tenant = tenants.find((t) => t.id === tenantId)
  const [value, setValue] = useState(tenant?.waba_phone_id ?? '')

  const save = async () => {
    const result = await action.run(() => tenantsApi.bindPhone(tenantId, value.trim()))
    if (result) {
      toast.push('Phone number bound')
      reload()
    }
  }

  return (
    <Card>
      <CardHeader
        title="Inbound routing"
        description="The WhatsApp number that routes messages to this tenant. A number can only be bound once."
        icon={<Phone className="h-4 w-4" />}
      />
      <CardBody className="space-y-3">
        <Input
          label="WABA phone number id"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="100012345678901"
          disabled={!can('manage_operations')}
        />
        {action.error && <Alert tone="danger">{action.error}</Alert>}
        <Button
          variant="secondary"
          size="sm"
          loading={action.busy}
          disabled={!can('manage_operations') || !value.trim()}
          onClick={save}
        >
          Save binding
        </Button>
      </CardBody>
    </Card>
  )
}
