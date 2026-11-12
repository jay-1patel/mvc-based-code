import { type ReactNode, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Blocks,
  Building2,
  ChevronDown,
  ClipboardCheck,
  KeyRound,
  LayoutGrid,
  LogOut,
  Menu,
  PlusCircle,
  ScrollText,
  Send,
  ShieldCheck,
  UserRound,
  Users,
  X,
} from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { useTenantProfile, useTenants } from '@/lib/tenants'
import { useOfferingsCount } from '@/lib/offerings'
import { buildTenantNav, NAV_GROUP_ORDER, type NavItem } from '@/lib/navigation'
import { conversationsApi } from '@/lib/conversations'
import { navigate, type RouteMatch } from '@/lib/router'
import { isOnboardingLocked, TEAM_PERMISSION, TENANT_PERMISSION, usePermissions } from '@/lib/permissions'
import { getVertical } from '@/lib/verticals'
import { cn } from '@/lib/cn'
import { CountBadge } from '@/components/remake/ui'
import { VerticalGlyph } from '@/components/vertical-icon'

/**
 * Views with a split pane own the full height of the content area. The panes
 * scroll on their own, so the window itself never grows a scrollbar and the
 * list and the transcript stay side by side at a fixed height.
 */
const FLUSH_VIEWS = new Set(['chat-history', 'inbox'])

/**
 * The sidebar is built per tenant rather than hard-coded: `buildTenantNav`
 * weighs the tenant's vertical, its feature flags and the signed-in admin's
 * permissions, and groups the screens by the job being done. See
 * `@/lib/navigation`.
 */
export function AppShell({ route, children }: { route: RouteMatch; children: ReactNode }) {
  const { identity, logout } = useAuth()
  const { can } = usePermissions()
  const { tenants, active, setActive } = useTenants()

  // The page follows the hash. The switcher must follow the same tenant,
  // otherwise the sidebar describes a different brand than the screen.
  useEffect(() => {
    if (route.tenantId && route.tenantId !== active?.id) {
      setActive(route.tenantId)
    }
  }, [route.tenantId, active?.id, setActive])
  const [switcherOpen, setSwitcherOpen] = useState(false)
  // Below `lg` the sidebar lives behind a hamburger; this tracks that drawer.
  const [navOpen, setNavOpen] = useState(false)

  // Any navigation collapses the drawer so it never covers the page asked for.
  useEffect(() => {
    setNavOpen(false)
    setSwitcherOpen(false)
  }, [route.view, route.tenantId])

  // Escape dismisses the drawer without waiting for a navigation.
  useEffect(() => {
    if (!navOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  const tenantScoped = Boolean(route.tenantId)
  const locked = isOnboardingLocked(identity)
  const isSuperAdmin = identity?.role === 'super_admin'
  const flush = FLUSH_VIEWS.has(route.view)
  // The header shows the tenant's own brand for admins and sub admins. Super
  // admins (who span tenants) keep the generic product mark instead.
  const chromeTenantId =
    route.tenantId ?? (!isSuperAdmin ? identity?.tenant_id ?? null : null)
  const profile = useTenantProfile(chromeTenantId)
  const features = profile?.features ?? null
  const logoUrl = profile?.brand?.logo_url?.trim() || null
  const brandName =
    profile?.brand?.name?.trim() || active?.display_name || active?.id || identity?.tenant_id || ''
  const brandTagline = profile?.brand?.tagline?.trim() || (active ? getVertical(active.vertical).short : '')
  const nav = locked ? [] : buildTenantNav({ vertical: active?.vertical, features, can })
  const contentCount = useOfferingsCount(
    route.tenantId && can('view_products') ? route.tenantId : null,
  )

  // Waiting-for-a-human count for the Chats badge. Polled gently; the
  // inbox screen itself polls faster once open.
  const canSeeInbox = tenantScoped && can('view_inbox')
  const [waitingChats, setWaitingChats] = useState(0)
  useEffect(() => {
    if (!canSeeInbox || !route.tenantId) {
      setWaitingChats(0)
      return
    }
    let alive = true
    const load = () =>
      conversationsApi
        .inbox(route.tenantId!, false)
        .then((r) => {
          if (alive) {
            setWaitingChats(
              r.handoff_count ?? r.queue.filter((q) => q.handover_mode === 'human').length,
            )
          }
        })
        .catch(() => {})
    load()
    const timer = window.setInterval(load, 30_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [canSeeInbox, route.tenantId])

  const navItem = (item: NavItem) => {
    const Icon = item.icon
    const isActive = route.view === item.id
    return (
      <button
        key={`${item.id}-${item.label}`}
        type="button"
        onClick={() => navigate(`/tenants/${encodeURIComponent(route.tenantId!)}/${item.id}`)}
        className={cn(
          'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition',
          isActive
            ? 'bg-accsoft font-semibold text-acc'
            : 'text-txmuted hover:bg-paper hover:text-tx',
        )}
      >
        <Icon className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
        {item.id === 'inbox' && <CountBadge count={waitingChats} tone="bad" className="ml-0" />}
        {item.showsCount && item.id === 'records' && contentCount !== null && (
          <span className="shrink-0 text-xs text-txfaint">{contentCount}</span>
        )}
      </button>
    )
  }

  const globalItem = (
    key: string,
    label: string,
    Icon: typeof Send,
    href: string,
    activeView: string,
    badge?: ReactNode,
  ) => (
    <button
      key={key}
      type="button"
      onClick={() => navigate(href)}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition',
        route.view === activeView
          ? 'bg-accsoft font-semibold text-acc'
          : 'text-txmuted hover:bg-paper hover:text-tx',
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      {badge}
    </button>
  )

  return (
    <div className="flex h-full bg-paper text-tx">
      {/* Small / medium: the sidebar is an off-canvas drawer behind a hamburger. */}
      {navOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-tx/40 lg:hidden"
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-line bg-sidebg transition-transform duration-200 ease-out',
          navOpen ? 'translate-x-0 shadow-2xl' : '-translate-x-full',
          'lg:static lg:z-auto lg:translate-x-0 lg:shadow-none',
        )}
      >
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-accsoft font-bold text-acc ring-1 ring-inset ring-acc/25">
            {!isSuperAdmin && logoUrl ? (
              <img src={logoUrl} alt={brandName || 'Company logo'} className="h-full w-full object-contain" />
            ) : !isSuperAdmin && brandName ? (
              <span className="text-sm">{brandName.slice(0, 1).toUpperCase()}</span>
            ) : (
              <Blocks className="h-4 w-4" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            {!isSuperAdmin ? (
              <>
                <p className="truncate text-sm font-semibold text-tx">
                  {brandName || 'Company console'}
                </p>
                <p className="truncate text-xs text-txmuted">{brandTagline || 'WhatsApp console'}</p>
              </>
            ) : (
              <>
                <p className="truncate text-sm font-semibold text-tx">Tenant Console</p>
                <p className="text-xs text-txmuted">Multi-tenant WhatsApp</p>
              </>
            )}
          </div>
          <span className="hidden shrink-0 items-center gap-1 text-xs font-medium text-ok lg:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" />
            Live
          </span>
          <button
            type="button"
            onClick={() => setNavOpen(false)}
            aria-label="Close navigation"
            title="Close navigation"
            className="shrink-0 rounded-md p-1.5 text-txmuted transition hover:bg-paper hover:text-tx lg:hidden"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-3">
          {locked ? null : can(TENANT_PERMISSION) || identity?.role === 'admin' ? (
            <button
              type="button"
              onClick={() => navigate('/register')}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-acc px-3.5 py-2 text-sm font-semibold text-onacc transition hover:opacity-90"
            >
              <PlusCircle className="h-4 w-4" />
              Register a tenant
            </button>
          ) : (
            <p className="rounded-lg bg-card px-3 py-2.5 text-xs leading-relaxed text-txmuted ring-1 ring-line">
              Read-only access. Ask a super admin for the manage_operations permission to register or edit
              tenants.
            </p>
          )}
        </div>

        {identity?.role === 'super_admin' && (
          <div className="relative mt-4 px-3">
            <button
              type="button"
              onClick={() => setSwitcherOpen((v) => !v)}
              className="flex w-full items-center gap-2.5 rounded-lg bg-card px-3 py-2.5 text-left ring-1 ring-line transition hover:bg-paper"
            >
              <VerticalGlyph name={getVertical(active?.vertical).icon} className="h-4 w-4 shrink-0 text-acc" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-tx">
                  {active?.display_name || active?.id || 'No tenant'}
                </span>
                <span className="block truncate text-xs text-txmuted">
                  {active ? getVertical(active.vertical).short : 'select a tenant'}
                </span>
              </span>
              <ChevronDown className={cn('h-4 w-4 shrink-0 text-txmuted transition', switcherOpen && 'rotate-180')} />
            </button>

            {switcherOpen && (
              <div className="absolute left-3 right-3 top-full z-30 mt-1 overflow-hidden rounded-lg bg-card shadow-xl ring-1 ring-line">
                <button
                  type="button"
                  onClick={() => {
                    setSwitcherOpen(false)
                    navigate('/tenants')
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-tx hover:bg-paper"
                >
                  <LayoutGrid className="h-3.5 w-3.5" />
                  All tenants
                  <span className="ml-auto text-txmuted">{tenants.length}</span>
                </button>
                <div className="max-h-72 overflow-y-auto scroll-thin">
                  {tenants.map((tenant) => (
                    <button
                      key={tenant.id}
                      type="button"
                      onClick={() => {
                        setActive(tenant.id)
                        setSwitcherOpen(false)
                        navigate(`/tenants/${encodeURIComponent(tenant.id)}/overview`)
                      }}
                      className={cn(
                        'flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-paper',
                        tenant.id === active?.id ? 'text-acc' : 'text-tx',
                      )}
                    >
                      <VerticalGlyph name={getVertical(tenant.vertical).icon} className="h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0 flex-1 truncate">{tenant.display_name || tenant.id}</span>
                      {tenant.current_version ? (
                        <span className="shrink-0 text-xs text-txmuted">v{tenant.current_version}</span>
                      ) : null}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <nav className="mt-4 flex-1 space-y-4 overflow-y-auto px-3 scroll-thin">
          {locked ? (
            <div className="space-y-0.5">
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                Onboarding
              </p>
              <button
                type="button"
                onClick={() => navigate('/register')}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition',
                  route.view === 'register'
                    ? 'bg-accsoft font-semibold text-acc'
                    : 'text-txmuted hover:bg-paper hover:text-tx',
                )}
              >
                <PlusCircle className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">Register a tenant</span>
              </button>
              <button
                type="button"
                onClick={() => navigate('/tenant-requests')}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition',
                  route.view === 'tenant-requests'
                    ? 'bg-accsoft font-semibold text-acc'
                    : 'text-txmuted hover:bg-paper hover:text-tx',
                )}
              >
                <Send className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">My change requests</span>
              </button>
              <p className="px-3 pt-2 text-xs leading-relaxed text-txmuted">
                Your registration is awaiting super admin approval. Once it is approved, the full
                dashboard unlocks.
              </p>
            </div>
          ) : tenantScoped && route.tenantId ? (
            <div className="space-y-4">
              {NAV_GROUP_ORDER.map((group) => {
                const items = nav.filter((i) => i.group === group)
                if (!items.length) return null
                return (
                  <div key={group} className="space-y-0.5">
                    <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                      {group}
                    </p>
                    {items.map(navItem)}
                  </div>
                )
              })}
            </div>
          ) : identity?.role !== 'super_admin' && identity?.tenant_id ? (
            <div className="space-y-0.5">
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                Your tenant
              </p>
              <button
                type="button"
                onClick={() => navigate(`/tenants/${encodeURIComponent(identity.tenant_id!)}/overview`)}
                className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-txmuted transition hover:bg-paper hover:text-tx"
              >
                <ArrowLeft className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">
                  {tenants.find((t) => t.id === identity.tenant_id)?.display_name || identity.tenant_id}
                </span>
              </button>
            </div>
          ) : (
            <p className="px-3 text-xs leading-relaxed text-txmuted">
              Select a tenant to configure its profile, or register a new one.
            </p>
          )}

          {!locked && isSuperAdmin && (
            <div className="space-y-0.5 border-t border-line pt-3">
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                Platform
              </p>
              {globalItem('businesses', 'Businesses', Building2, '/tenants', '__tenants__')}
              {globalItem('tenant-requests', 'Business requests', ClipboardCheck, '/tenant-requests', 'tenant-requests')}
              {globalItem('api-requests', 'API requests', ShieldCheck, '/api-requests', 'api-requests')}
              {globalItem('audit-history', 'Activity log', ScrollText, '/audit-history', 'audit-history')}
              {globalItem('errors', 'Errors & health', AlertTriangle, '/errors', 'errors')}
            </div>
          )}

          {!locked && !isSuperAdmin && identity && (can('manage_operations') || identity.role === 'admin') && (
            <div className="space-y-0.5 border-t border-line pt-3">
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                Manage
              </p>
              {identity.role === 'admin' && (
                <>
                  {globalItem('audit-history', 'Activity log', ScrollText, '/audit-history', 'audit-history')}
                  {globalItem('errors', 'Errors & health', AlertTriangle, '/errors', 'errors')}
                </>
              )}
              {identity.tenant_id &&
                globalItem(
                  'api-access',
                  'API access',
                  KeyRound,
                  `/tenants/${encodeURIComponent(identity.tenant_id)}/api-access`,
                  'api-access',
                )}
              {globalItem('tenant-requests', 'My change requests', Send, '/tenant-requests', 'tenant-requests')}
            </div>
          )}

          {can(TEAM_PERMISSION) && !locked && (
            <div className="space-y-0.5 border-t border-line pt-3">
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-txfaint">
                Manage
              </p>
              {globalItem('team', 'People', Users, '/team', 'team')}
            </div>
          )}
        </nav>

        <div className="border-t border-line p-3">
          <div className="flex items-center gap-2.5 rounded-lg px-2 py-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accsoft text-acc ring-1 ring-inset ring-acc/25">
              <UserRound className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-tx">{identity?.username}</p>
              <span
                className={cn(
                  'mt-0.5 inline-block rounded-full px-2 py-px text-xs font-medium',
                  identity?.role === 'sub_admin' ? 'bg-chipdefbg text-txmuted' : 'bg-accsoft text-acc',
                )}
              >
                {identity?.role === 'super_admin' ? 'super admin' : identity?.role === 'admin' ? 'admin' : 'sub admin'}
              </span>
            </div>
            <button
              type="button"
              onClick={logout}
              title="Sign out"
              className="shrink-0 rounded-md p-1.5 text-txmuted transition hover:bg-paper hover:text-badink"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <main
        className={cn(
          'min-w-0 flex-1 bg-paper scroll-thin',
          flush
            ? 'flex flex-col overflow-hidden'
            : cn('overflow-x-hidden overflow-y-auto', navOpen && 'overflow-hidden lg:overflow-y-auto'),
        )}
      >
        {/* Small / medium: a sticky top bar with the hamburger and the brand. */}
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-sidebg px-4 lg:hidden">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            title="Open navigation"
            className="rounded-md p-1.5 text-txmuted transition hover:bg-paper hover:text-tx"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-accsoft font-bold text-acc ring-1 ring-inset ring-acc/25">
              {!isSuperAdmin && logoUrl ? (
                <img src={logoUrl} alt={brandName || 'Company logo'} className="h-full w-full object-contain" />
              ) : !isSuperAdmin && brandName ? (
                <span className="text-sm">{brandName.slice(0, 1).toUpperCase()}</span>
              ) : (
                <Blocks className="h-4 w-4" />
              )}
            </div>
            <p className="truncate text-sm font-semibold text-tx">
              {isSuperAdmin ? 'Tenant Console' : brandName || 'Company console'}
            </p>
          </div>
        </header>

        <div
          className={cn(
            'mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8',
            flush && 'flex min-h-0 w-full flex-1 flex-col',
          )}
        >
          {isSuperAdmin && route.tenantId && (
            <div
              role="note"
              className="mb-4 rounded-lg bg-chipwebbg px-4 py-2.5 text-xs text-chipwebink"
            >
              View-only — changes come from the tenant's admin and are approved by you.
            </div>
          )}
          {children}
        </div>
      </main>
    </div>
  )
}
