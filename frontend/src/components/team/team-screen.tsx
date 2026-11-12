import { useState } from 'react'
import { ArrowLeft, KeyRound, Loader2, Pencil, Plus, ShieldCheck, Trash2, UserRound, X } from 'lucide-react'
import { adminsApi, type AdminRecord } from '@/lib/admins'
import { navigate } from '@/lib/router'
import {
  ALL_PERMISSIONS,
  matchingPreset,
  PERMISSION_GROUPS,
  permissionGroups,
  PERMISSION_LABELS,
  presetPermissions,
  SUB_ADMIN_PRESETS,
  usePermissions,
} from '@/lib/permissions'
import { useTenants } from '@/lib/tenants'
import { useAction, useAsync } from '@/lib/hooks'
import { formatDate, initials } from '@/lib/format'
import { cn } from '@/lib/cn'
import {
  Card,
  DangerButton,
  EmptyState,
  ErrorBanner,
  FieldInput,
  FieldSelect,
  GhostButton,
  IconButton,
  Notice,
  Panel,
  Pill,
  PrimaryButton,
  Toggle,
} from '@/components/remake/ui'
import { LoadingBlock } from '@/components/ui/feedback'
import { PageHeader } from '@/components/layout/page-header'
import { useToast } from '@/components/ui/toast'
import { PASSWORD_HINT, passwordError } from '@/lib/password'

type Draft = {
  username: string
  email: string
  password: string
  tenantId: string
  role: 'super_admin' | 'admin' | 'sub_admin'
  permissions: Record<string, boolean>
}

/** The compact size the legacy `size="sm"` buttons had on this screen. */
const smallBtn = 'px-3 py-1.5 text-xs'

export function TeamScreen() {
  const { identity, isSuperAdmin, canManageTeam } = usePermissions()
  const { tenants } = useTenants()
  const admins = useAsync(() => adminsApi.list(), [])
  const action = useAction()
  const toast = useToast()

  /** Only super admins pick a tenant; everyone else inherits their own. */
  const ownTenantId = identity?.tenant_id ?? ''

  const emptyDraft = (): Draft => ({
    username: '',
    email: '',
    password: '',
    tenantId: isSuperAdmin ? '' : ownTenantId,
    role: 'sub_admin',
    permissions: Object.fromEntries(ALL_PERMISSIONS.map((p) => [p, false])),
  })

  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [editing, setEditing] = useState<string | null>(null)
  const [editPerms, setEditPerms] = useState<Record<string, boolean>>({})
  const [editTenant, setEditTenant] = useState('')
  const [resetting, setResetting] = useState<string | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)

  if (!canManageTeam) {
    return (
      <Notice tone="warn" title="Not permitted">
        Your account does not hold the manage_admins permission, so the team screen is not available.
      </Notice>
    )
  }

  const create = async () => {
    const result = await action.run(() =>
      adminsApi.create({
        username: draft.username.trim(),
        password: draft.password,
        role: draft.role,
        // Sub admins get exactly the switches above. Admins are created with
        // every permission on (server default); a super admin edits them down
        // afterwards from this same screen.
        permissions: draft.role === 'sub_admin' ? draft.permissions : null,
        email: draft.email.trim() || null,
        // Super admins choose; the server forces everyone else to their own tenant.
        tenant_id: isSuperAdmin ? draft.tenantId.trim() || null : ownTenantId || null,
      }),
    )
    if (result) {
      toast.push(`Created ${result.username}`)
      setCreating(false)
      setDraft(emptyDraft())
      admins.reload()
    }
  }

  /** Vertical of a tenant, so the permission matrix matches what it manages. */
  const verticalOf = (tenantId?: string | null) => tenants.find((t) => t.id === tenantId)?.vertical

  const startEdit = (admin: AdminRecord) => {
    setEditing(admin.username)
    setEditPerms({ ...admin.permissions })
    setEditTenant(admin.tenant_id ?? '')
  }

  const saveEdit = async (admin: AdminRecord) => {
    const body: { permissions?: Record<string, boolean>; tenant_id?: string | null } = {}
    if (admin.role !== 'super_admin') body.permissions = editPerms
    if (isSuperAdmin && (editTenant || null) !== (admin.tenant_id ?? null)) {
      body.tenant_id = editTenant || null
    }
    const result = await action.run(() => adminsApi.update(admin.username, body))
    if (result) {
      toast.push(`Saved changes for ${admin.username}`)
      setEditing(null)
      admins.reload()
    }
  }

  const reset = async (username: string) => {
    const problem = passwordError(newPassword)
    if (problem) {
      toast.push(problem)
      return
    }
    const result = await action.run(() => adminsApi.resetPassword(username, newPassword))
    if (result) {
      toast.push(`Password reset for ${username}`)
      setResetting(null)
      setNewPassword('')
    }
  }

  const remove = async (username: string) => {
    const result = await action.run(() => adminsApi.remove(username))
    if (result) {
      toast.push(`Removed ${username}`)
      setDeleting(null)
      admins.reload()
    }
  }

  /** Where the Back button goes: the admin's own tenant, or the tenant list. */
  const backTarget =
    identity?.role !== 'super_admin' && identity?.tenant_id
      ? `/tenants/${encodeURIComponent(identity.tenant_id)}/overview`
      : '/tenants'

  return (
    <div>
      <PageHeader
        title="Team & permissions"
        description="Every admin sees this console through their own permissions: a screen or an action they do not hold is never offered. The server re-checks all of it — this is the operator's view, not the enforcement point."
        actions={
          <div className="flex items-center gap-2">
            <GhostButton
              className={smallBtn}
              onClick={() => navigate(backTarget)}
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </GhostButton>
            <PrimaryButton
              className={smallBtn}
              onClick={() => {
                setCreating((v) => !v)
                setDraft(emptyDraft())
              }}
            >
              <Plus className="h-4 w-4" />
              New admin
            </PrimaryButton>
          </div>
        }
        meta={
          <Pill tone={isSuperAdmin ? 'ok' : 'neutral'}>
            {isSuperAdmin ? 'You are a super admin — full control' : 'You are a sub admin'}
          </Pill>
        }
      />

      {action.error && <ErrorBanner title="Action failed" message={action.error} className="mb-4" />}

      {creating && (
        <Panel
          title="New admin"
          className="mb-4"
          action={
            <IconButton onClick={() => setCreating(false)} title="Close">
              <X className="h-4 w-4" />
            </IconButton>
          }
        >
          <p className="-mt-1 mb-4 flex items-center gap-2 text-sm text-txmuted">
            <UserRound className="h-4 w-4" />
            A sub admin gets exactly the switches you turn on here.
          </p>
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <FieldInput
                label="Username"
                value={draft.username}
                onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                autoFocus
              />
              <FieldInput
                label="Email"
                type="email"
                value={draft.email}
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
                hint="Required in valid format"
              />
              <FieldInput
                label="New password"
                type="password"
                value={draft.password}
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
                placeholder="Enter new password"
                hint={
                  draft.password && passwordError(draft.password)
                    ? passwordError(draft.password) ?? PASSWORD_HINT
                    : PASSWORD_HINT
                }
              />
            </div>
            <FieldSelect
              label="Tenant"
              value={isSuperAdmin ? draft.tenantId : ownTenantId}
              onChange={(e) => setDraft({ ...draft, tenantId: e.target.value })}
              disabled={!isSuperAdmin}
              hint={
                isSuperAdmin
                  ? 'The tenant this admin is scoped to. Leave empty for a tenant-wide super admin.'
                  : 'Automatically set to your tenant — only a super admin can choose it.'
              }
            >
              {isSuperAdmin ? (
                <>
                  <option value="">No tenant (unscoped)</option>
                  {tenants.map((tenant) => (
                    <option key={tenant.id} value={tenant.id}>
                      {tenant.display_name || tenant.id}
                    </option>
                  ))}
                </>
              ) : (
                <option value={ownTenantId}>{ownTenantId || 'Your account has no tenant'}</option>
              )}
            </FieldSelect>
            <FieldSelect
              label="Role"
              value={draft.role}
              onChange={(e) => setDraft({ ...draft, role: e.target.value as Draft['role'] })}
              hint={isSuperAdmin ? undefined : (identity?.role === 'admin' ? 'Admin can create sub-admin only' : 'Only a super admin can grant super/admin roles.')}
              disabled={identity?.role === 'sub_admin'}
            >
              <option value="sub_admin">Sub admin — permissions apply</option>
              {isSuperAdmin && <option value="admin">Admin — all permissions for their tenant</option>}
            </FieldSelect>

            {draft.role === 'sub_admin' && (
              <>
                <PresetRow
                  value={draft.permissions}
                  onChange={(permissions) => setDraft({ ...draft, permissions })}
                />
                <PermissionMatrix
                  groups={permissionGroups(verticalOf(isSuperAdmin ? draft.tenantId : ownTenantId))}
                  value={draft.permissions}
                  onChange={(permissions) => setDraft({ ...draft, permissions })}
                />
              </>
            )}

            <div className="flex justify-end gap-2">
              <GhostButton onClick={() => setCreating(false)}>
                Cancel
              </GhostButton>
              <PrimaryButton
                disabled={
                  action.busy ||
                  !draft.username.trim() ||
                  !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.email.trim()) ||
                  passwordError(draft.password) !== null
                }
                onClick={create}
              >
                {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Create admin
              </PrimaryButton>
            </div>
          </div>
        </Panel>
      )}

      {admins.loading ? (
        <LoadingBlock label="Loading the team…" />
      ) : (admins.data ?? []).length === 0 ? (
        <Card>
          <EmptyState title="No admins" description="Create the first account to sign in." />
        </Card>
      ) : (
        <div className="space-y-3">
          {admins.data!.map((admin) => {
            const isSelf = admin.username === identity?.username
            const isEditing = editing === admin.username
            const granted = ALL_PERMISSIONS.filter((p) => admin.permissions?.[p])

            return (
              <Card key={admin.username} className="p-5">
                <div className="flex flex-wrap items-start gap-4">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accsoft text-sm font-semibold text-acc ring-1 ring-inset ring-line">
                    {initials(admin.username)}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-semibold text-tx">{admin.username}</span>
                      {admin.role === 'super_admin' ? (
                        <Pill tone="ok">
                          <span className="inline-flex items-center gap-1">
                            <ShieldCheck className="h-3 w-3" />
                            super admin
                          </span>
                        </Pill>
                      ) : admin.role === 'admin' ? (
                        <Pill tone="ok">admin</Pill>
                      ) : (
                        <Pill>sub admin</Pill>
                      )}
                      {isSelf && <Pill>you</Pill>}
                    </div>
                    <p className="mt-1 text-xs text-txfaint">
                      {admin.email || 'no email'} · joined {formatDate(admin.created_at)}{admin.tenant_id ? ` · ${admin.tenant_id}` : ''}
                    </p>
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {admin.role === 'super_admin' ? (
                        <span className="text-xs text-txmuted">All permissions, by role.</span>
                      ) : granted.length ? (
                        granted.map((p) => (
                          <Pill key={p}>
                            {PERMISSION_LABELS[p]}
                          </Pill>
                        ))
                      ) : (
                        <span className="text-xs text-attink">No permissions — this admin cannot do anything.</span>
                      )}
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    {!isSelf && (admin.role === 'sub_admin' || (admin.role === 'admin' && isSuperAdmin)) && (
                      <GhostButton className={smallBtn} onClick={() => startEdit(admin)}>
                        <Pencil className="h-4 w-4" />
                        Edit
                      </GhostButton>
                    )}
                    {resetting === admin.username ? (
                      <>
                        <div className="w-48">
                          <FieldInput
                            type="password"
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                            placeholder="New password"
                            hint={newPassword ? passwordError(newPassword) ?? undefined : PASSWORD_HINT}
                          />
                        </div>
                        <PrimaryButton
                          className={smallBtn}
                          disabled={action.busy || passwordError(newPassword) !== null}
                          onClick={() => reset(admin.username)}
                        >
                          {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                          Save
                        </PrimaryButton>
                        <GhostButton className={smallBtn} onClick={() => setResetting(null)}>
                          Cancel
                        </GhostButton>
                      </>
                    ) : (
                      !isSelf && (
                        <GhostButton className={smallBtn} onClick={() => setResetting(admin.username)}>
                          <KeyRound className="h-4 w-4" />
                          Password
                        </GhostButton>
                      )
                    )}
                    {(admin.role === 'sub_admin' || (admin.role === 'admin' && isSuperAdmin)) &&
                      (deleting === admin.username ? (
                        <>
                          <DangerButton className={smallBtn} disabled={action.busy} onClick={() => remove(admin.username)}>
                            {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                            Confirm
                          </DangerButton>
                          <GhostButton className={smallBtn} onClick={() => setDeleting(null)}>
                            Cancel
                          </GhostButton>
                        </>
                      ) : (
                        !isSelf && (
                          <IconButton danger title={`Remove ${admin.username}`} onClick={() => setDeleting(admin.username)}>
                            <Trash2 className="h-4 w-4" />
                          </IconButton>
                        )
                      ))}
                  </div>
                </div>

                {isSelf && (
                  <p className="mt-4 text-xs text-txmuted">
                    Your own role and permissions are locked — ask another admin with manage_admins to change them.
                  </p>
                )}

                {isEditing && (
                  <div className="mt-4 border-t border-linesoft pt-4">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-base font-semibold text-tx">Edit {admin.username}</h3>
                      <div className="flex gap-2">
                        <GhostButton className={smallBtn} onClick={() => setEditPerms(Object.fromEntries(ALL_PERMISSIONS.map((p) => [p, true])))}>
                          All on
                        </GhostButton>
                        <GhostButton className={smallBtn} onClick={() => setEditPerms(Object.fromEntries(ALL_PERMISSIONS.map((p) => [p, false])))}>
                          All off
                        </GhostButton>
                      </div>
                    </div>
                    {isSuperAdmin ? (
                      <div className="mb-4 max-w-sm">
                        <FieldSelect
                          label="Tenant"
                          value={editTenant}
                          onChange={(e) => setEditTenant(e.target.value)}
                          hint="Re-scopes this admin to another tenant."
                        >
                          <option value="">No tenant (unscoped)</option>
                          {tenants.map((tenant) => (
                            <option key={tenant.id} value={tenant.id}>
                              {tenant.display_name || tenant.id}
                            </option>
                          ))}
                        </FieldSelect>
                      </div>
                    ) : (
                      <p className="mb-4 text-xs text-txmuted">
                        Tenant is fixed to yours ({ownTenantId || 'none'}) — only a super admin can re-scope an admin.
                      </p>
                    )}
                    {admin.role === 'sub_admin' && (
                      <PresetRow value={editPerms} onChange={setEditPerms} />
                    )}
                    <PermissionMatrix
                      groups={permissionGroups(verticalOf(editTenant || admin.tenant_id))}
                      value={editPerms}
                      onChange={setEditPerms}
                    />
                    <div className="mt-4 flex justify-end gap-2">
                      <GhostButton onClick={() => setEditing(null)}>
                        Cancel
                      </GhostButton>
                      <PrimaryButton disabled={action.busy} onClick={() => saveEdit(admin)}>
                        {action.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                        Save changes
                      </PrimaryButton>
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * One-tap sub-admin presets (Support / Content / Operations / Growth from
 * the role-panels design). A preset only pre-ticks switches — the admin can
 * still adjust them, and any manual change turns the selection into Custom.
 * Roles are fixed at creation, so there is deliberately no role control
 * anywhere on this screen: the role is the pill on each person's card.
 */
function PresetRow({
  value,
  onChange,
}: {
  value: Record<string, boolean>
  onChange: (next: Record<string, boolean>) => void
}) {
  const active = matchingPreset(value)
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <span className="text-xs text-txmuted">Preset:</span>
      {SUB_ADMIN_PRESETS.map((preset) => (
        <button
          key={preset.id}
          type="button"
          title={preset.description}
          onClick={() => onChange(presetPermissions(preset.id))}
          className={cn(
            'inline-flex items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition',
            active === preset.id
              ? 'bg-acc font-semibold text-onacc hover:opacity-90'
              : 'border border-line bg-card font-medium text-tx hover:bg-paper',
          )}
        >
          {preset.label}
        </button>
      ))}
      <span className="text-xs text-txmuted">
        {active === 'custom' ? 'Custom — tick the switches yourself.' : 'Pre-ticked switches — adjust any of them before saving.'}
      </span>
    </div>
  )
}

function PermissionMatrix({
  groups = PERMISSION_GROUPS,
  value,
  onChange,
}: {
  /** Vertical-specific groups — see `permissionGroups` in lib/permissions. */
  groups?: { label: string; permissions: string[] }[]
  value: Record<string, boolean>
  onChange: (next: Record<string, boolean>) => void
}) {
  return (
    <div className="grid gap-4 rounded-panel border border-line bg-paper p-4 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map((group) => (
        <div key={group.label}>
          <h3 className="mb-2 text-sm font-semibold text-tx">{group.label}</h3>
          <div className="space-y-2">
            {group.permissions.map((permission) => (
              <Toggle
                key={permission}
                size="sm"
                checked={Boolean(value[permission])}
                onChange={(next) => onChange({ ...value, [permission]: next })}
                label={PERMISSION_LABELS[permission]}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
