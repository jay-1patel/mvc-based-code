import { useState, type ReactNode } from 'react'
import { FlaskConical, Loader2, Phone, Play, ShieldCheck } from 'lucide-react'
import { useAction, useAsync } from '@/lib/hooks'
import { tenantsApi, useTenants } from '@/lib/tenants'
import { useAuth } from '@/lib/auth'
import type { SmokeReport } from '@/lib/types'
import { Card, ErrorBanner, FieldInput, GhostButton, Notice, Pill, PrimaryButton } from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
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

function SectionHeader({
  icon,
  title,
  description,
  actions,
}: {
  icon: ReactNode
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-linesoft px-4 py-3 sm:px-5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-txmuted">{icon}</span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-tx">{title}</h2>
          {description && <p className="text-xs text-txmuted">{description}</p>}
        </div>
      </div>
      {actions}
    </div>
  )
}

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
      <SectionHeader
        title="Test a question"
        description="Rules first, embeddings second — the same two tiers the eval harness drives."
        icon={<FlaskConical className="h-4 w-4" />}
      />
      <div className="space-y-4 p-4 sm:p-5">
        <div className="flex gap-2">
          <div className="flex-1">
            <FieldInput
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run(message)}
              placeholder="Type what a customer would say…"
            />
          </div>
          <PrimaryButton onClick={() => run(message)} disabled={action.busy}>
            {action.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run
          </PrimaryButton>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {SAMPLES.map((sample) => (
            <button
              key={sample}
              type="button"
              onClick={() => run(sample)}
              className="rounded-md border border-line px-2 py-1 text-xs text-txmuted transition hover:bg-accsoft hover:text-tx"
            >
              {sample}
            </button>
          ))}
        </div>

        {action.error && <ErrorBanner message={action.error} />}

        {result && (
          <div className="space-y-3 rounded-panel bg-paper p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-txmuted">“{result.message}” →</span>
              {result.intent ? (
                <Pill tone="ok">{result.intent}</Pill>
              ) : (
                <Pill tone="warn">no intent matched</Pill>
              )}
              <Pill>score {result.score}</Pill>
              <Pill>tier: {result.tier}</Pill>
              {result.flow && <Pill tone="ok">flow: {result.flow}</Pill>}
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-txmuted">
                Routable intents ({result.active_intents.length})
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {result.active_intents.map((name) => (
                  <span
                    key={name}
                    className={
                      name === result.intent
                        ? 'rounded bg-accsoft px-1.5 py-0.5 text-xs font-medium text-acc'
                        : 'rounded bg-card px-1.5 py-0.5 text-xs text-txmuted'
                    }
                  >
                    {name}
                  </span>
                ))}
                {!result.active_intents.length && (
                  <span className="text-xs text-txmuted">none — this tenant cannot route anything</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}

function SmokeCard({ tenantId }: { tenantId: string }) {
  const action = useAction()
  const smoke = useAsync<SmokeReport>(() => tenantsApi.smoke(tenantId), [tenantId])

  return (
    <Card>
      <SectionHeader
        title="Smoke test"
        description="Asserts menus and intents equal the profile, not a hardcoded constant."
        icon={<ShieldCheck className="h-4 w-4" />}
        actions={
          <GhostButton onClick={smoke.reload} disabled={action.busy || smoke.loading} className="px-3 py-1.5 text-xs">
            {(action.busy || smoke.loading) && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Run
          </GhostButton>
        }
      />
      <div className="space-y-3 p-4 sm:p-5">
        {smoke.error && <ErrorBanner message={smoke.error} />}
        {smoke.loading && !smoke.data ? (
          <LoadingBlock label="Running…" />
        ) : smoke.data ? (
          <>
            <div className="flex items-center gap-2">
              {smoke.data.ok ? (
                <Pill tone="ok">passed</Pill>
              ) : (
                <Pill tone="bad">failed</Pill>
              )}
              <span className="text-xs text-txmuted">
                {smoke.data.vertical} · v{smoke.data.version}
              </span>
            </div>
            {smoke.data.forbidden_term_leaks.length > 0 && (
              <Notice tone="bad" title="Forbidden terms leaked into the profile copy">
                {smoke.data.forbidden_term_leaks.join(', ')}
              </Notice>
            )}
            <div>
              <p className="text-xs uppercase tracking-wide text-txmuted">
                Visible buttons ({smoke.data.visible_buttons.length})
              </p>
              <p className="mt-1 font-mono text-xs text-txmuted">
                {smoke.data.visible_buttons.join(' · ') || 'none'}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-txmuted">Intent gates</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {Object.entries(smoke.data.gated_intents).map(([intent, on]) => (
                  <Pill key={intent} tone={on ? 'ok' : 'neutral'}>
                    {intent}: {on ? 'open' : 'closed'}
                  </Pill>
                ))}
              </div>
            </div>
          </>
        ) : (
          <p className="text-xs text-txmuted">Run the smoke test to check this tenant.</p>
        )}
      </div>
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
      <SectionHeader
        title="Inbound routing"
        description="The WhatsApp number that routes messages to this tenant. A number can only be bound once."
        icon={<Phone className="h-4 w-4" />}
      />
      <div className="space-y-3 p-4 sm:p-5">
        <FieldInput
          label="WABA phone number id"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="100012345678901"
          disabled={!can('manage_operations')}
        />
        {action.error && <ErrorBanner message={action.error} />}
        <GhostButton
          onClick={save}
          disabled={!can('manage_operations') || !value.trim() || action.busy}
          className="px-3 py-1.5 text-xs"
        >
          {action.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save binding
        </GhostButton>
      </div>
    </Card>
  )
}
