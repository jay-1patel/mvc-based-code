/**
 * Permissions, mirrored from `ALL_PERMISSIONS` in `backend/routes/auth.py`.
 *
 * Two separate things use this:
 *   - the console decides which screens and buttons a signed-in admin sees, and
 *   - the Team screen renders one switch per permission, grouped for readability.
 *
 * The server re-checks every one of these. Hiding a button is politeness; the
 * authorisation that matters is in `require_permission` / `require_tenant_access`.
 */

import { useAuth } from './auth'

export const PERMISSION_GROUPS: { label: string; permissions: string[] }[] = [
  {
    label: 'Tenant configuration',
    permissions: ['manage_operations'],
  },
  {
    label: 'Team',
    permissions: ['manage_admins'],
  },
  {
    label: 'Content',
    permissions: ['upload_faq', 'upload_kb', 'view_files', 'delete_files'],
  },
  {
    label: 'Products & brochure',
    permissions: [
      'view_products',
      'edit_delete_products',
      'catalogue_new_arrival',
      'view_distributors',
      'manage_distributors',
    ],
  },
  {
    label: 'Conversations',
    permissions: ['chat', 'chat_history', 'view_inbox'],
  },
  {
    label: 'Operations',
    permissions: ['view_orders', 'manage_orders', 'view_complaints', 'manage_complaints', 'view_customers'],
  },
  {
    label: 'Growth',
    permissions: ['view_campaigns', 'manage_campaigns', 'view_analytics'],
  },
]

/** Company info pages (Projects / Technologies / Careers / Benefits) — IT only. */
const IT_COMPANY_INFO_PERMS = [
  'manage_projects',
  'manage_technologies',
  'manage_careers',
  'manage_benefits',
] as const

/**
 * The catalogue + pages an admin manages, per vertical. A shop tenant's team
 * gets add/edit/delete product switches; a software & IT tenant's team gets
 * services plus company info pages. Banking / finance / healthcare etc. do
 * not get Technologies or Careers — those belong to IT menus only.
 */
function contentGroupsFor(vertical: string | null | undefined): { label: string; permissions: string[] } {
  if (vertical === 'ecommerce') {
    return {
      label: 'Products & brochure',
      permissions: [
        'view_products',
        'add_product',
        'edit_product',
        'delete_product',
        'catalogue_new_arrival',
        'view_distributors',
        'manage_distributors',
      ],
    }
  }
  if (vertical === 'it_software') {
    return {
      label: 'Services & company pages',
      permissions: [
        'view_products',
        'manage_services',
        ...IT_COMPANY_INFO_PERMS,
      ],
    }
  }
  if (vertical === 'banking') {
    return {
      label: 'Banking products & accounts',
      permissions: [
        'view_products',
        'add_product',
        'edit_product',
        'delete_product',
        'catalogue_new_arrival',
      ],
    }
  }
  if (vertical === 'finance') {
    return {
      label: 'Financial services & advisory',
      permissions: [
        'view_products',
        'add_product',
        'edit_product',
        'delete_product',
        'manage_services',
        'catalogue_new_arrival',
      ],
    }
  }
  if (vertical === 'tours_travel') {
    return {
      label: 'Tour packages & travel',
      permissions: [
        'view_products',
        'add_product',
        'edit_product',
        'delete_product',
        'catalogue_new_arrival',
      ],
    }
  }
  if (vertical === 'healthcare') {
    return {
      label: 'Treatments & healthcare services',
      permissions: [
        'view_products',
        'add_product',
        'edit_product',
        'delete_product',
        'manage_services',
      ],
    }
  }
  return {
    label: 'Offerings & catalog',
    permissions: [
      'view_products',
      'add_product',
      'edit_product',
      'delete_product',
    ],
  }
}

/**
 * The permission groups to render for a tenant of a given vertical.
 * Non-commerce verticals (healthcare, banking, finance, tours_travel, it_software)
 * do not see campaigns, distributors, or orders.
 */
export function permissionGroups(vertical?: string | null): { label: string; permissions: string[] }[] {
  const isCommerce = vertical === 'ecommerce'

  const operationsPerms = isCommerce
    ? ['view_orders', 'manage_orders', 'view_complaints', 'manage_complaints', 'view_customers']
    : ['view_complaints', 'manage_complaints', 'view_customers']

  const hasCampaigns =
    vertical === 'ecommerce' ||
    vertical === 'banking' ||
    vertical === 'finance' ||
    vertical === 'tours_travel'

  const growthGroup = hasCampaigns
    ? {
        label: 'Growth',
        permissions: ['view_campaigns', 'manage_campaigns', 'view_analytics'],
      }
    : {
        label: 'Analytics',
        permissions: ['view_analytics'],
      }

  return [
    {
      label: 'Tenant configuration',
      permissions: ['manage_operations'],
    },
    {
      label: 'Team',
      permissions: ['manage_admins'],
    },
    {
      label: 'Content',
      permissions: ['upload_faq', 'upload_kb', 'view_files', 'delete_files'],
    },
    contentGroupsFor(vertical),
    {
      label: 'Conversations',
      permissions: ['chat', 'chat_history', 'view_inbox'],
    },
    {
      label: 'Operations',
      permissions: operationsPerms,
    },
    growthGroup,
  ]
}

export const PERMISSION_LABELS: Record<string, string> = {
  manage_admins: 'Manage admins',
  upload_faq: 'Upload FAQ files',
  upload_kb: 'Upload KB files',
  view_files: 'View uploaded files',
  delete_files: 'Delete uploaded files',
  view_products: 'View products',
  edit_delete_products: 'Edit/delete products',
  manage_operations: 'Manage operations (profiles, menus, tenants)',
  chat: 'Use admin chat',
  chat_history: 'View chat history',
  catalogue_new_arrival: 'Manage brochure and new releases',
  view_orders: 'View orders',
  manage_orders: 'Edit/delete orders',
  view_complaints: 'View complaints',
  manage_complaints: 'Handle complaints',
  view_customers: 'View customers',
  view_analytics: 'View analytics',
  view_inbox: 'Live inbox',
  view_campaigns: 'Campaigns',
  manage_campaigns: 'Edit/delete campaigns',
  view_distributors: 'Distributors',
  manage_distributors: 'Add/edit/delete distributors',
  add_product: 'Add catalogue entries',
  edit_product: 'Edit catalogue entries',
  delete_product: 'Remove catalogue entries',
  manage_services: 'Add/edit/delete services',
  manage_projects: 'Edit the projects page',
  manage_technologies: 'Edit the technologies page',
  manage_careers: 'Edit the careers page',
  manage_benefits: 'Edit the benefits page',
}

export const ALL_PERMISSIONS = Object.keys(PERMISSION_LABELS)

/** Reading or changing anything tenant-scoped needs manage_operations. */
export const TENANT_PERMISSION = 'manage_operations'
/** The team screen needs manage_admins. */
export const TEAM_PERMISSION = 'manage_admins'

/**
 * Which permissions allow each write action on a tenant's catalogue, mirroring
 * `_require_write_access` in `backend/routes/offerings.py`. `edit_delete_products`
 * remains the all-in-one grant; vertical-specific grants can be finer.
 */
export function contentWritePerms(vertical?: string | null): {
  add: string[]
  edit: string[]
  remove: string[]
} {
  if (vertical === 'it_software' || vertical === 'finance' || vertical === 'healthcare') {
    const all = ['edit_delete_products', 'manage_services', 'add_product', 'edit_product']
    return { add: all, edit: all, remove: ['edit_delete_products', 'delete_product'] }
  }
  return {
    add: ['edit_delete_products', 'add_product'],
    edit: ['edit_delete_products', 'edit_product'],
    remove: ['edit_delete_products', 'delete_product'],
  }
}

/**
 * First-login lock: a company admin stays in onboarding until a super admin
 * approves one of the tenant change requests they filed from "Register a
 * tenant". Until then they only get "Register a tenant" and "My change
 * requests". Super admins and sub admins are never gated, and a missing flag
 * (older backend) is treated as unlocked.
 */
export function isOnboardingLocked(
  identity: { role?: string | null; onboarding_approved?: boolean } | null,
): boolean {
  if (!identity || identity.role !== 'admin') return false
  return identity.onboarding_approved === false
}

/**
 * Permission switches that WRITE a tenant's own data (upload, edit, publish,
 * send, reply, mint, delete...). The decided role rule: a super admin is
 * VIEW-ONLY inside tenants, so for them these switches never open a write
 * control — even though the role technically holds every switch. Read-type
 * switches (view_*, chat_history) and system powers (manage_admins, tenant
 * registration via manage_operations at the registry level) are unaffected;
 * panels gate their write controls with `canWriteTenant`, not `can`.
 * The server refuses the same writes with 403 (backend/routes/auth.py).
 */
export const TENANT_WRITE_PERMISSIONS = [
  'upload_faq',
  'upload_kb',
  'delete_files',
  'edit_delete_products',
  'add_product',
  'edit_product',
  'delete_product',
  'manage_services',
  'manage_projects',
  'manage_technologies',
  'manage_careers',
  'manage_benefits',
  'manage_operations',
  'chat',
  'catalogue_new_arrival',
  'manage_orders',
  'manage_complaints',
  'manage_campaigns',
  'manage_distributors',
]

/**
 * Sub-admin presets (decided 2026-11-10): one-tap bundles of the real
 * switches from the role-panels design §3. A preset only pre-ticks switches
 * in the Team create/edit form — the admin can still adjust before saving,
 * and any manual change makes the selection "Custom". Every delete-type
 * switch (delete_files, delete_product, edit_delete_products) stays OFF in
 * every preset. `manage_services` and the IT company-page switches are the
 * Content preset's vertical switches; they only take effect for tenants
 * whose vertical exposes those pages.
 */
export const SUB_ADMIN_PRESETS: {
  id: string
  label: string
  description: string
  permissions: string[]
}[] = [
  {
    id: 'support',
    label: 'Support',
    description: 'Inbox, chat history, complaints, customers (view)',
    permissions: ['view_inbox', 'chat_history', 'view_complaints', 'manage_complaints', 'view_customers', 'chat'],
  },
  {
    id: 'content',
    label: 'Content',
    description: 'Uploads, catalogue and content pages',
    permissions: [
      'view_files', 'upload_faq', 'upload_kb', 'view_products',
      'add_product', 'edit_product', 'manage_services',
      'manage_projects', 'manage_technologies', 'manage_careers', 'manage_benefits',
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    description: 'Orders, customers, distributors',
    permissions: ['view_orders', 'manage_orders', 'view_customers', 'view_distributors'],
  },
  {
    id: 'growth',
    label: 'Growth',
    description: 'Campaigns and analytics',
    permissions: ['view_campaigns', 'manage_campaigns', 'view_analytics'],
  },
]

/** The full switch set a preset applies: preset switches ON, everything else OFF. */
export function presetPermissions(presetId: string): Record<string, boolean> {
  const preset = SUB_ADMIN_PRESETS.find((p) => p.id === presetId)
  const on = new Set(preset?.permissions ?? [])
  return Object.fromEntries(ALL_PERMISSIONS.map((p) => [p, on.has(p)]))
}

/** Which preset exactly matches a switch set, or 'custom' when none does. */
export function matchingPreset(permissions: Record<string, boolean>): string {
  for (const preset of SUB_ADMIN_PRESETS) {
    const candidate = presetPermissions(preset.id)
    if (ALL_PERMISSIONS.every((p) => Boolean(permissions[p]) === candidate[p])) return preset.id
  }
  return 'custom'
}

export function usePermissions() {
  const { can, identity } = useAuth()
  const isSuperAdmin = identity?.role === 'super_admin'
  return {
    identity,
    isSuperAdmin,
  isAdmin: identity?.role === 'admin',
  isSubAdmin: identity?.role === 'sub_admin',
  can,
    canManageTenants: can(TENANT_PERMISSION),
    canManageTeam: can(TEAM_PERMISSION),
    /**
     * Whether a write control for a tenant-data action may be offered.
     * Identical to `can` for admins and sub admins; always false for a
     * super admin on a tenant-write switch (view-only rule).
     */
    canWriteTenant: (permission: string) =>
      isSuperAdmin && TENANT_WRITE_PERMISSIONS.includes(permission) ? false : can(permission),
  }
}
