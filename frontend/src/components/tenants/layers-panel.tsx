import { useState } from 'react'
import { FileJson, Layers } from 'lucide-react'
import { useTenantDetail } from './hooks'
import { Card, Pill } from '@/components/remake/ui'
import { Alert, LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { cn } from '@/lib/cn'

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
            <Pill tone="ok">current v{data.current_version ?? 0}</Pill>
            {data.effective_error && <Pill tone="bad">{data.effective_error}</Pill>}
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
              className={cn(
                'w-full rounded-panel border p-3 text-left transition',
                selected === layer.id
                  ? 'border-acc bg-accsoft'
                  : 'border-line bg-card hover:bg-paper',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-tx">{layer.label}</span>
                <Pill>{layer.size}</Pill>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-txmuted">{layer.hint}</p>
            </button>
          ))}
        </div>

        <Card className="lg:col-span-3">
          <div className="flex items-center gap-2 border-b border-linesoft px-4 py-3 sm:px-5">
            <span className="text-txmuted">
              {selected === 'effective' ? <Layers className="h-4 w-4" /> : <FileJson className="h-4 w-4" />}
            </span>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-tx">{selected}</h2>
              <p className="text-xs text-txmuted">Raw JSON exactly as stored.</p>
            </div>
          </div>
          <div className="p-4 sm:p-5">
            <pre className="max-h-[32rem] overflow-auto rounded-panel bg-paper p-4 font-mono text-xs leading-relaxed text-txmuted">
              {payload ? JSON.stringify(payload, null, 2) : 'This layer is empty.'}
            </pre>
          </div>
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
