/**
 * The tenant sidebar, as data — remake edition.
 *
 * The remake groups the same screens by the job the admin is doing
 * (Today / Talk to customers / Your business data / Grow / Manage /
 * Settings → Advanced) and renames them in plain words. Every item still
 * maps to the SAME route view it always did; only the label, the icon
 * and the grouping changed. Visibility is unchanged too: an item shows
 * when the admin's permissions and the tenant's feature flags allow it,
 * exactly as before.
 *
 * Route mapping (view id → new label):
 *   overview          → Today                    records   → <Products|Services|…> & prices
 *   inbox             → Chats                    uploads   → Knowledge
 *   chat-history      → Past chats               uploads   → Files (same screen, second door)
 *   complaints        → Help requests            website-knowledge → Website knowledge
 *   callbacks         → Callbacks                customers → Customers
 *   menu              → Bot menu                 orders    → Orders
 *   distributors      → Partners                 campaigns → Send an offer
 *   intents           → Questions the bot understands
 *   versions          → Versions                 tokens    → Connections
 *   profile           → Profile (Advanced)       menu-edit → Menu editor (Advanced)
 *   layers            → Layers (Advanced)        api-access → API access (Advanced)
 *   chat              → Admin chat (Advanced)    test      → Test & smoke (Advanced)
 *   whatsapp-tester   → WhatsApp Tester (Advanced)
 *   projects/technologies/careers/benefits keep their names (IT tenants).
 */

import {
  AlertTriangle,
  BookOpen,
  Briefcase,
  ClipboardList,
  Cpu,
  FileText,
  FlaskConical,
  FolderOpen,
  Gift,
  Globe,
  HelpCircle,
  History,
  Inbox,
  KeyRound,
  Layers,
  LayoutGrid,
  ListTree,
  Megaphone,
  MessagesSquare,
  Package,
  Phone,
  Plug,
  ScrollText,
  ShieldCheck,
  ShoppingCart,
  Truck,
  Users,
  type LucideIcon,
} from 'lucide-react'
import type { FeatureFlag, Features } from './types'
import { getVertical } from './verticals'

export interface NavItem {
  /** Route view id: `#/tenants/<tenant>/<id>`. */
  id: string
  label: string
  icon: LucideIcon
  /** Sidebar group heading this item sits under. */
  group: NavGroup
  /** Visible when the admin holds any of these. Undefined is ungated. */
  permissions?: string[]
  /** Visible when the tenant has this feature on. Undefined is ungated. */
  feature?: FeatureFlag
  /** True when the sidebar should show the tenant's record count. */
  showsCount?: boolean
}

export type NavGroup =
  | 'Today'
  | 'Talk to customers'
  | 'Your business data'
  | 'Grow'
  | 'Manage'
  | 'Settings → Advanced'

export const NAV_GROUP_ORDER: NavGroup[] = [
  'Today',
  'Talk to customers',
  'Your business data',
  'Grow',
  'Manage',
  'Settings → Advanced',
]

/**
 * Company info pages for Software & IT tenants only (tech stack, portfolio,
 * hiring, why-us). Other verticals use their own menus — banking has products /
 * eligibility, not Technologies or Careers.
 */
const IT_COMPANY_INFO_ITEMS: Omit<NavItem, 'group'>[] = [
  { id: 'projects', label: 'Projects', icon: FolderOpen, permissions: ['manage_projects'] },
  { id: 'technologies', label: 'Technologies', icon: Cpu, permissions: ['manage_technologies'] },
  { id: 'careers', label: 'Careers', icon: Briefcase, permissions: ['manage_careers'] },
  { id: 'benefits', label: 'Benefits', icon: Gift, permissions: ['manage_benefits'] },
]

/** Verticals that show the IT company-info sidebar pages. */
const VERTICALS_WITH_COMPANY_INFO = new Set(['it_software'])

/**
 * Build the sidebar for one tenant.
 *
 * `features` may be null while the resolved profile is loading (or when the
 * admin lacks `manage_operations`, which that endpoint needs). In that case the
 * feature gate is skipped and only permissions decide — showing a live inbox to
 * someone who cannot open it is a smaller sin than hiding every screen until a
 * request the user may not be allowed to make resolves.
 */
export function buildTenantNav(opts: {
  vertical?: string | null
  features?: Features | null
  can: (permission: string) => boolean
}): NavItem[] {
  const content = getVertical(opts.vertical).content
  const features = opts.features ?? null
  const companyInfo = VERTICALS_WITH_COMPANY_INFO.has(opts.vertical ?? '')
    ? IT_COMPANY_INFO_ITEMS.map((item) => ({ ...item, group: 'Your business data' as NavGroup }))
    : []

  const uploadPermissions = ['upload_faq', 'upload_kb', 'view_files', 'catalogue_new_arrival']

  const items: NavItem[] = [
    { id: 'overview', label: 'Today', icon: LayoutGrid, group: 'Today' },

    // Talk to customers
    {
      id: 'inbox',
      label: 'Chats',
      icon: Inbox,
      group: 'Talk to customers',
      permissions: ['view_inbox'],
      feature: 'human_handover',
    },
    {
      id: 'chat-history',
      label: 'Past chats',
      icon: History,
      group: 'Talk to customers',
      permissions: ['chat_history'],
    },
    {
      id: 'complaints',
      label: 'Help requests',
      icon: AlertTriangle,
      group: 'Talk to customers',
      permissions: ['view_complaints'],
      feature: 'complaints',
    },
    {
      id: 'callbacks',
      label: 'Callbacks',
      icon: Phone,
      group: 'Talk to customers',
      permissions: ['manage_operations'],
      feature: 'callback',
      showsCount: true,
    },

    // Your business data
    { id: 'menu', label: 'Bot menu', icon: ListTree, group: 'Your business data', permissions: ['manage_operations'] },
    {
      id: 'records',
      label: `${content.label} & prices`,
      icon: Package,
      group: 'Your business data',
      permissions: ['view_products'],
      feature: 'offerings',
      showsCount: true,
    },
    {
      id: 'uploads',
      label: 'Knowledge',
      icon: BookOpen,
      group: 'Your business data',
      permissions: uploadPermissions,
    },
    {
      // Website Knowledge: the crawl half of onboarding (KB / FAQs /
      // product knowledge from the tenant's website). Same content
      // switches as Uploads — the two screens are one knowledge hub.
      id: 'website-knowledge',
      label: 'Website knowledge',
      icon: Globe,
      group: 'Your business data',
      permissions: uploadPermissions,
    },
    {
      // Files is the same Uploads screen through a second door: that
      // screen manages both the knowledge content and the raw files, and
      // the remake's plain naming keeps both jobs findable.
      id: 'uploads',
      label: 'Files',
      icon: FileText,
      group: 'Your business data',
      permissions: uploadPermissions,
    },
    ...companyInfo,
    { id: 'customers', label: 'Customers', icon: Users, group: 'Your business data', permissions: ['view_customers'] },
    {
      id: 'orders',
      label: 'Orders',
      icon: ShoppingCart,
      group: 'Your business data',
      permissions: ['view_orders'],
      feature: 'orders',
    },
    {
      id: 'distributors',
      label: 'Partners',
      icon: Truck,
      group: 'Your business data',
      permissions: ['view_distributors'],
      feature: 'distributors',
    },

    // Grow
    {
      id: 'campaigns',
      label: 'Send an offer',
      icon: Megaphone,
      group: 'Grow',
      permissions: ['view_campaigns'],
      feature: 'campaigns',
    },
    {
      id: 'intents',
      label: 'Questions the bot understands',
      icon: HelpCircle,
      group: 'Grow',
      permissions: ['manage_operations'],
    },

    // Manage
    { id: 'versions', label: 'Versions', icon: ClipboardList, group: 'Manage', permissions: ['manage_operations'] },
    { id: 'tokens', label: 'Connections', icon: KeyRound, group: 'Manage', permissions: ['manage_operations'] },

    // Settings → Advanced: the technical screens, kept reachable but out
    // of the daily path.
    { id: 'profile', label: 'Profile', icon: ScrollText, group: 'Settings → Advanced', permissions: ['manage_operations'] },
    { id: 'menu-edit', label: 'Menu editor', icon: ListTree, group: 'Settings → Advanced', permissions: ['manage_operations'] },
    { id: 'layers', label: 'Layers', icon: Layers, group: 'Settings → Advanced', permissions: ['manage_operations'] },
    {
      id: 'api-access',
      label: 'API access',
      icon: Plug,
      group: 'Settings → Advanced',
      permissions: ['manage_operations'],
    },
    {
      id: 'chat',
      label: 'Admin chat',
      icon: MessagesSquare,
      group: 'Settings → Advanced',
      permissions: ['chat'],
    },
    {
      id: 'test',
      label: 'Test & smoke',
      icon: ShieldCheck,
      group: 'Settings → Advanced',
      permissions: ['manage_operations'],
    },
    {
      id: 'whatsapp-tester',
      label: 'WhatsApp Tester',
      icon: FlaskConical,
      group: 'Settings → Advanced',
      permissions: ['manage_operations'],
    },
  ]
  return items.filter((item) => isVisible(item, opts.can, features))
}

/** Whether one item passes its permission and feature gates. */
export function isVisible(
  item: NavItem,
  can: (permission: string) => boolean,
  features: Features | null,
): boolean {
  if (item.permissions && !item.permissions.some((p) => can(p))) return false
  if (item.feature && features && features[item.feature] === false) return false
  return true
}

/** The ids a known view can resolve to — used to decide "unknown view". */
export function knownViews(vertical?: string | null): string[] {
  return buildTenantNav({ vertical, features: null, can: () => true }).map((i) => i.id)
}
