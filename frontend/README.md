# Tenant Console

React + Tailwind admin for the multi-tenant WhatsApp platform. Light theme, 17px base
type. It registers tenants, builds their profile layer, and publishes versioned
profiles to the bot.

```bash
npm install
npm run dev        # http://localhost:5173
npm run typecheck
npm run build
```

The dev server proxies `/api`, `/catalog` and `/ws` to `http://localhost:9000`
(override with `BACKEND_ORIGIN`). The backend's CORS allow-list does not include
`:5173`, so the proxy is the supported path — do not call the API cross-origin.

## The idea

A tenant is one business. Everything the bot does for it — its menu, the words it
uses, the intents it can match, the flows it can run, and what it must never say —
comes from that tenant's profile. This console is where that profile is written.

Registration is a questionnaire rather than a form dump: you pick the **business
field** (e-commerce, software & IT, tours & travel, banking, finance, healthcare,
generic) and the wizard then asks only the questions that matter for that field,
offers the capabilities that field supports, and previews what the bot will be able
to do. The same vertical drives the vocabulary — a software tenant's offerings are
`services`, a travel tenant's are `packages` — so the rest of the app reads the
tenant's own words back from the published profile.

## Screens

| Route | What it does |
| --- | --- |
| `#/tenants` | Registry: every tenant, its field, live version, phone binding |
| `#/register` | The onboarding questionnaire, then create + publish |
| `#/tenants/:id/overview` | The live profile as the bot sees it |
| `#/tenants/:id/profile` | Editor: brand, vocabulary, hours, notifications, capabilities, guardrails |
| `#/tenants/:id/menu` | Menu rendering, with gated buttons marked hidden |
| `#/tenants/:id/menu-edit` | Add, edit, reorder and **delete** menu options |
| `#/tenants/:id/flows` | Active vs gated intents, and every live flow's steps |
| `#/tenants/:id/versions` | Publish, inspect and roll back profile versions |
| `#/tenants/:id/layers` | defaults → client file → published DB layer, and the merged result |
| `#/tenants/:id/tokens` | Tenant-scoped API tokens (mint, copy-once, revoke) |
| `#/tenants/:id/test` | Test-a-question against the live matcher, smoke report, phone binding |
| `#/team` | Team & permissions: create admins, grant permissions, reset passwords, delete |

## The sidebar is tenant-aware

The sidebar is not a static list. Under the console screens it renders the
selected tenant's **live WhatsApp menu** and its **live capabilities**, both read
from that tenant's published profile:

- `leewaysoftech` → Our Services · Technologies · Portfolio · Service Brochure ·
  Careers · Get a Quote · Book a Callback · Support · Talk to Human
- `wanderly` → Destinations · Packages · Travel Brochure · Booking Policy ·
  Visa & Documents · Enquire · Book a Callback · Support · Talk to Human
- `troogood` → View Products · Brochure · New Releases · policies · Cart · …

Each option shows its own section heading, a `9/9 live` count, and an eye-off
marker on anything whose `requires_feature` is currently off — that option is not
reaching customers right now. Clicking one jumps straight into the menu editor
with that button loaded (`#/tenants/:id/menu-edit?focus=<button id>`), or to the
read-only menu for an admin without `manage_operations`.

## Permissions

Permissions are mirrored from `ALL_PERMISSIONS` in `backend/routes/auth.py` into
`src/lib/permissions.ts`. Two separate things use them:

- **Visibility.** A super admin sees everything. A sub admin sees only the screens
  and actions their switches cover: no `manage_operations` means no tenant editing,
  no tenant registration, no menu editing, and read-only rows elsewhere. The nav
  hides those items and a deep link to one is refused, not just disabled.
- **Delegation.** Under Team & permissions a super admin creates admins and sets
  each permission individually (or all on / all off). A sub admin can be given
  `manage_admins` too — then it appears for them, minus the things only a super
  admin may do: promoting roles, demoting a super admin, and editing its own row.

The server re-checks everything in `require_permission` / `require_tenant_access`.
The frontend gating is the operator's view of that, not the enforcement point.

## Two rules that shape the editors

- **Lists replace, they do not append** — except for keyed lists. Menu buttons,
  intents and flows merge *by id*, so editing one keeps the rest, and **deleting an
  inherited button sends `{"id": "…", "__remove__": true}`**. A layer never blanks a
  lower layer with an empty value, so the menu editor warns when you clear an
  inherited `requires_feature` / `flow` / `intent` rather than pretending it stuck.
- **Publishing is the only thing the bot sees.** Saving a draft changes nothing at
  runtime. Publish validates, versions and purges the cache; rollback re-points at an
  earlier version and deletes nothing.

## Layout

```
src/
  lib/        api client, auth + tenant contexts, hash router, permissions,
              vertical catalog, wizard ⇄ profile-snapshot mapping
  components/
    ui/       button, input, select, switch, tags, card, badge, feedback, toast
    layout/   app shell (sidebar, tenant switcher), page header
    auth/     sign-in and first-run super admin setup
    onboarding/  the vertical-aware registration questionnaire
    tenants/  every tenant-scoped screen
    team/     admins and their permissions
```
