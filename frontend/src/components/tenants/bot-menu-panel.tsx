import { GripVertical, Pencil } from 'lucide-react'
import { usePermissions } from '@/lib/permissions'
import { navigate } from '@/lib/router'
import { menuButtonSource } from '@/lib/source'
import { GhostButton, Panel, Pill, SourceChip } from '@/components/remake/ui'
import { Alert, LoadingBlock } from '@/components/ui/feedback'
import { useResolved, useTenantDetail } from './hooks'

/**
 * Bot menu — what customers see, in the new shell.
 *
 * Read-first by design: it shows the published menu exactly as the bot
 * will present it, each row labelled with where the item came from
 * (your PDF, typed by an admin, or the system default). Editing still
 * happens in the Menu editor, because in this system menu changes go
 * through draft → publish (or a change request for non-super roles) —
 * there is no safe instant toggle, so the switch on each row reflects
 * the item's on/off state and opens the editor for writers.
 */
export function BotMenuPanel({ tenantId }: { tenantId: string }) {
  const { canWriteTenant, isSuperAdmin } = usePermissions()
  const canWrite = canWriteTenant('manage_content')
  const resolved = useResolved(tenantId)
  const detail = useTenantDetail(tenantId)

  const data = resolved.data
  const buttons = [...(data?.menu?.buttons ?? [])].sort((a, b) => a.sort_order - b.sort_order)
  const quickReply = buttons.length <= 3

  const openEditor = (id: string) =>
    navigate(`/tenants/${tenantId}/menu-edit?focus=${encodeURIComponent(id)}`)

  return (
    <div>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-tx">Bot menu</h1>
            {data?.version ? <Pill tone="ok">Published · v{data.version}</Pill> : <Pill>Not published</Pill>}
          </div>
          <p className="mt-1 text-sm text-txmuted">
            Menu items come from your PDF or from you — never from a website crawl.
          </p>
        </div>
        {canWrite && (
          <GhostButton onClick={() => navigate(`/tenants/${tenantId}/menu-edit`)}>
            <Pencil className="h-4 w-4" /> Edit in Menu editor
          </GhostButton>
        )}
      </header>

      {resolved.error && (
        <Alert tone="danger" title="Could not load the menu" className="mb-4">
          {resolved.error}
        </Alert>
      )}
      {detail.data?.has_draft && (
        <Alert tone="warning" title="A draft is in progress" className="mb-4">
          The menu below is the published one. The draft is edited and published from Versions.
        </Alert>
      )}

      {resolved.loading ? (
        <LoadingBlock label="Loading the menu…" />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[1fr_300px]">
          <Panel title="Published menu">
            {buttons.length === 0 ? (
              <p className="py-2 text-sm text-txmuted">
                No menu buttons are published. The bot is using the system default menu.
              </p>
            ) : (
              <ul>
                {buttons.map((button) => {
                  const source = menuButtonSource(button, detail.data)
                  return (
                    <li
                      key={button.id}
                      className="flex items-center gap-3 border-b border-linesoft py-3 last:border-b-0"
                    >
                      <GripVertical className="h-4 w-4 shrink-0 text-txfaint" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-tx">{button.title}</p>
                        {button.description && (
                          <p className="truncate text-xs text-txmuted">{button.description}</p>
                        )}
                      </div>
                      <SourceChip source={source} />
                      {canWrite ? (
                        <button
                          type="button"
                          role="switch"
                          aria-checked={button.enabled}
                          aria-label={`${button.title} — ${button.enabled ? 'on' : 'off'}. Open in the Menu editor to change.`}
                          title="Menu changes go through draft and publish — open the editor"
                          onClick={() => openEditor(button.id)}
                          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                            button.enabled ? 'bg-acc' : 'bg-switchoff'
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow ${
                              button.enabled ? 'right-0.5' : 'left-0.5'
                            }`}
                          />
                        </button>
                      ) : (
                        <span
                          role="img"
                          aria-label={button.enabled ? 'On' : 'Off'}
                          className={`relative h-6 w-11 shrink-0 rounded-full ${
                            button.enabled ? 'bg-acc' : 'bg-switchoff'
                          }`}
                        >
                          <span
                            className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow ${
                              button.enabled ? 'right-0.5' : 'left-0.5'
                            }`}
                          />
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            <p className="mt-3 text-xs text-txmuted">
              {isSuperAdmin
                ? 'View-only — the tenant’s admin edits the menu; you approve the change.'
                : 'To change the menu — add, rename, reorder, switch off — use the Menu editor; changes publish as a new version.'}
            </p>
          </Panel>

          {/* phone preview — same Smart Switch rule as the bot: <=3 quick replies, else a list */}
          <div className="rounded-panel border border-line bg-paper p-4">
            <p className="mb-3 text-center text-[11px] font-semibold uppercase tracking-wide text-txmuted">
              What the customer sees
            </p>
            <div className="rounded-panel border border-line bg-card p-3.5">
              <p className="whitespace-pre-line text-sm text-tx">
                {data?.menu?.body || 'Welcome! How can we help you today?'}
              </p>
              <p className="mt-1 text-[11px] text-txfaint">
                {quickReply ? 'Shown as quick-reply buttons' : 'Shown as a tap-to-open list'}
              </p>
              <div className="mt-3 space-y-1.5">
                {buttons
                  .filter((b) => b.enabled)
                  .map((b) =>
                    quickReply ? (
                      <span
                        key={b.id}
                        className="block rounded-full border border-line bg-paper px-3 py-1.5 text-center text-xs font-medium text-acc"
                      >
                        {b.title}
                      </span>
                    ) : (
                      <span
                        key={b.id}
                        className="block border-b border-linesoft px-1 py-1.5 text-sm text-tx last:border-b-0"
                      >
                        {b.title}
                      </span>
                    ),
                  )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
