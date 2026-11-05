import { useState } from 'react'
import { FileJson, Layers } from 'lucide-react'
import { useTenantDetail } from './hooks'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'

const LAYERS = [
  { id: 'defaults', label: 'Vertical defaults', hint: 'Shipped with the platform, chosen by the business field.' },
  { id: 'file', label: 'Client file', hint: 'clients/<tenant>/config.json on the server. Often empty.' },
  { id: 'db', label: 'Published overrides', hint: 'The current published version. This is the only DB layer the bot reads.' },
] as const

export function LayersPanel({ tenantId }: { tenantId: string }) {
  const { data, error, loading } = useTenantDetail(tenantId)
  const [selected, setSelected] = useState<string>('db')

  if (loading) return <LoadingBlock label="Loading layers…" />
  if (error || !data) {
    return (
      <Alert tone="danger" title="Could not load layers">
        {error ?? 'Unknown error'}
      </Alert>
    )
  }

  const layers = data.layers ?? { defaults: {}, file: {}, db: {} }
  const payload = selected === 'effective' ? data.effective : layers[selected as 'defaults' | 'file' | 'db']

  return (
    <div>
      <PageHeader
        title="Layers"
        description="A profile is merged in one direction only: vertical defaults, then the client file, then the published DB overrides. Later layers replace earlier ones; lists never append."
        meta={
          <>
            <Badge tone="accent">current v{data.current_version ?? 0}</Badge>
            {data.effective_error && <Badge tone="danger">{data.effective_error}</Badge>}
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-4">
        <div className="space-y-2 lg:col-span-1">
          {[...LAYERS.map((l) => ({ ...l, size: sizeOf(layers[l.id]) })), {
            id: 'effective',
            label: 'Effective (merged)',
            hint: 'What the bot actually reads right now.',
            size: sizeOf(data.effective),
          }].map((layer) => (
            <button
              key={layer.id}
              type="button"
              onClick={() => setSelected(layer.id)}
              className={`w-full rounded-lg p-3 text-left ring-1 ring-inset transition ${
                selected === layer.id
                  ? 'bg-accent-50 ring-accent-300'
                  : 'bg-surface-panel ring-surface-line hover:bg-accent-50'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-slate-100">{layer.label}</span>
                <Badge tone="muted">{layer.size}</Badge>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">{layer.hint}</p>
            </button>
          ))}
        </div>

        <Card className="lg:col-span-3">
          <CardHeader
            title={selected}
            description="Raw JSON exactly as stored."
            icon={selected === 'effective' ? <Layers className="h-4 w-4" /> : <FileJson className="h-4 w-4" />}
          />
          <CardBody>
            <pre className="max-h-[32rem] overflow-auto scroll-thin rounded-lg bg-surface p-4 font-mono text-xs leading-relaxed text-slate-400">
              {payload ? JSON.stringify(payload, null, 2) : 'This layer is empty.'}
            </pre>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

function sizeOf(value: unknown): string {
  if (!value || typeof value !== 'object') return 'empty'
  const text = JSON.stringify(value)
  return text.length > 999 ? `${(text.length / 1024).toFixed(1)}k chars` : `${text.length} chars`
}
