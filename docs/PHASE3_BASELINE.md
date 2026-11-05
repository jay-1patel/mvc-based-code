# BASELINE — Backend Roadmap Phase 3 (folder restructure in baby steps)

**Date:** 2026-11-05 · Recorded BEFORE any Phase 3 move, on the code that already
contains Phase 1 (error-finding core) and Phase 2 (single logging setup, no
silent errors). This phase moves code only; it does not change behaviour.

**Environment (identical for every number below and every per-step Gate):**
- Python: `~/.fcenv/bin/python` (3.12, FastAPI 0.115.0). Heavy ML dependencies
  (`sentence_transformers`, `faiss`) are **not installed on this machine**; a
  small in-process harness stubs only those two modules for boot checks. Three
  test files fail collection for that reason. The Gate rule is
  **like-for-like**: same commands, same environment, zero new failures.
- All boot checks use a throwaway database under temporary storage and disable
  the inbound poller. Production data is never used for a gate.

## (a) Backend test suite

Command: `cd backend && ~/.fcenv/bin/python -m pytest . -q --continue-on-collection-errors`

**Result: 185 passed · 64 failed · 8 errors · 3 subtests passed**
(257 collected test cases, 3 collection errors.)

Failures/errors by file at baseline (all pre-existing):

| File | Failed | Errors |
|---|---:|---:|
| test_campaigns.py | 14 | |
| test_tenant_approvals.py | 10 | |
| test_qa_section1_auth.py | 8 | |
| test_qa_section13_security.py | 6 | |
| test_distributors.py | 5 | |
| test_knowledge_gaps_and_async_uploads.py | | 5 |
| test_qa_b1_multi_tenant.py | 4 | |
| test_package_flow.py | 4 | |
| test_whatsapp_routing_dedup.py | 3 | |
| test_tenant_audit.py | 2 | |
| test_qa_section7_phone.py | 2 | |
| test_qa_section5_bifurcation.py | 2 | |
| test_menu_button_dedupe.py | 2 | |
| test_package_media.py | 1 | |
| test_audit.py | 1 | |
| test_audit_extended.py | | 1 (collection: ML dependencies) |
| test_destination_isolation_and_rag.py | | 1 (collection: ML dependencies) |
| test_faq_tenant_isolation.py | | 1 (collection: ML dependencies) |

The complete failed/error test-name set (72 names) was captured with this
baseline; every later Gate diffs against it name by name. After tests move,
names are matched by their class/test suffix so a path change alone is not a
false diff.

## (b) Boot and API count

- `GET /` → **200**, body `{"status":"Unified Bot Service running"}`
- `GET /docs` → **200**
- Live API operations counted from application routes: **294**
- Error drill: `POST /classify` with invalid JSON → **500 JSON** containing
  `detail`, `error`, `request_id`, and `api`; the response header request id
  matches the body.

## (c) Frontend reference points

- TypeScript: **12 known errors** (onboarding components only).
- Vitest: **37 passed / 5 failed** (known failures only).

## (d) Step 3.1 inventory — candidates for parking

| Candidate | Live importer proof at baseline |
|---|---|
| `routing/main.py` | No Python importer found |
| `kb/main.py` | No Python importer found |
| `backend/routes/tenant_admin.py` | Not registered by the app; no Python importer found |
| `backend/routes/tenant_webhook.py` | Not registered by the app; no Python importer found |
| `backend/routes/pdf_intake.py` | Not registered by the app; no Python importer found |
| `backend/routes/send2_webhook.py` | Not registered by the app; no Python importer found |
| `backend/database_multi_tenant.py` | **Has live importers** — tenant menu service, tenant orchestrator, and tenant knowledge service import it; two parked route files also import it. Parking therefore requires a working compatibility shim at the old location, or the file must stay. |

Out of scope for the whole phase: `backend/services/universal_file_bifurcator.py`
is a pre-existing corrupted non-Python artifact (it begins with a literal file
marker and cannot be parsed). It will not be touched, moved, or repaired here.

## (e) Step 3.2 inventory — test files and current locations

Forty-three files match `test_*.py` in application folders. One is **not** a
test: `backend/routes/test_simulator.py` is live application code (its router
is registered by the app) and is excluded from the move forever in this phase.

The 42 real test files all currently live directly in `backend/`:

test_active_test_tenant_routing.py · test_admin_tenant_scope.py ·
test_api_onboarding.py · test_audit.py · test_audit_context.py ·
test_audit_extended.py · test_bifurcation_guards.py · test_campaigns.py ·
test_cta_url_offerings.py · test_destination_isolation_and_rag.py ·
test_distributors.py · test_draft_ttl.py · test_e2e_products_qa.py ·
test_error_reporter.py · test_faq_tenant_isolation.py ·
test_feature_gating.py · test_handover_resume.py ·
test_knowledge_gaps_and_async_uploads.py · test_list_menu_row_ids.py ·
test_menu_button_dedupe.py · test_menu_flow_features.py ·
test_offerings_browse.py · test_offerings_media.py · test_package_flow.py ·
test_package_media.py · test_qa_b1_multi_tenant.py · test_qa_m1_m5_guards.py ·
test_qa_section10_media.py · test_qa_section13_security.py ·
test_qa_section1_auth.py · test_qa_section3_upload_matrix.py ·
test_qa_section4_extract.py · test_qa_section5_bifurcation.py ·
test_qa_section7_phone.py · test_scrub_foreign_menu.py ·
test_service_answers.py · test_tenant_approvals.py · test_tenant_audit.py ·
test_tenant_create_conflict.py · test_tenant_hard_delete.py ·
test_tenant_operations.py · test_whatsapp_routing_dedup.py

There is no existing pytest configuration file or test-package directory to
build on; tests currently rely on being run from inside `backend/` so that
application modules are importable as top-level modules.

## (f) Step 3.3 inventory — tenant data under the backend folder

`backend/clients/` holds 103 knowledge files across 14 tenant folders:
13, 14, 15, 99-destinations, 99destination,
leeway-softech-private-limited, leewaysoftech,
makemytrip-india-private-limited, meridian-bank, p, prestige-group, sbi,
skyline-realty-developers, skyline-realty-group.

Code references that can resolve to this location and must be audited in
Step 3.3:
- The tenancy store's tenant-pack deletion covers a backend-folder clients
  root in addition to the canonical tenant-pack root.
- A document-processing service builds a working-directory-relative
  `clients/<tenant>/knowledge` path; when the application runs from the
  backend folder, that resolves under `backend/clients/`.
- Knowledge tooling in the older bot tree uses a base-directory-relative
  `clients/` path (that base directory is the project root, a different
  location, and must not be silently retargeted).

Canonical tenant config packs are managed through a single path value with
an environment override; Step 3.3 must preserve override behaviour and route
the moved backend-folder data through one configuration value rather than
adding new hard-coded paths.

## (g) Step 3.4 inventory — the database facade and remaining bodies

`backend/database.py` is 2,483 lines. Most public queries already live in
`backend/db/` and are re-exported by the facade. The destination package has
16 modules: admins, audit, chat, config_drafts, core, crawl, gaps, menu,
products, search, sessions, settings, state, templates, webhooks, plus its
package initializer.

Function bodies still defined in the facade, grouped for the domain-by-domain
split:

| Group | Definitions (baseline line ranges) |
|---|---|
| Customers | `touch_customer` (126–172), `update_customer_profile` (175–195) |
| Core schema ladder | `init_db` (200–1173) — creates core chat/webhook/session/product/order/complaint/FAQ/knowledge/admin/campaign/cart tables and many migrations |
| Tenant schema and migrations | `_has_legacy_unique_on_phone_id` (1178–1192), `_rebuild_tenants_for_partial_unique` (1195–1244), `_init_tenancy_tables` (1247–1351), `_default_tenant_id_for_conn` (1849–1903), `_init_embedding_tenancy` (1906–1999), `_has_legacy_global_unique_on_slug` (2044–2052), `_default_tenant_id` (2171–2176), `_table_pk_columns` (2179–2192), `_migrate_composite_tenant_wa_keys` (2195–2404) |
| Onboarding and tenant changes | `_init_api_onboarding_tables` (1354–1413), `_init_tenant_change_tables` (1416–1476) |
| Callbacks | `_ensure_callback_tables` (1481–1712) |
| Integrations | `_init_integration_tables` (1715–1778) |
| Leads | `_init_lead_tables` (1783–1844) |
| Offerings and records | `_init_offerings_migration` (2004–2041), `_migrate_products_per_tenant_slug` (2055–2118), `_init_record_columns_table` (2123–2166) |
| Conversation state | `_init_conversation_state_columns` (2407–2432) |

The facade contract is absolute for this step: every existing
`from database import X` caller keeps working unchanged after each domain.

## (h) Step 3.5 inventory — the WhatsApp chain files

Traced from the application entry and the inbound listener, the live chain
currently spans four areas:

- **Routing package:** webhook, message, classifier, button actions, button
  registry (+ its JSON data), complaint flow, hookdeck tee, and the routing
  WhatsApp sender. The routing configuration and routing database modules are
  shared infrastructure imported well beyond WhatsApp and need an explicit
  stay/move decision with evidence.
- **Backend services:** incoming listener/poller, orchestrator, tenant
  orchestrator, flow runner, state manager, unified bot, WhatsApp sender and
  WhatsApp outbox.
- **Knowledge-bot tree:** the bot router and service entry, plus its services
  (knowledge handler, brain, retrieval, WhatsApp sender and formatter, menu
  catalog/handler/router/service, chat, cart, tools, dynamic router,
  embeddings, fuzzy matcher, product keywords, bot configuration, multi-tenant
  helper, tenant retrieval, template manager, queue, monitor, rate limiter,
  and authentication helpers used by that tree).
- **Bot flow packages:** every Python module under the customer and
  distributor flow packages (menus, workflows, tools, cart, and the
  distributor catalog/finance/order/ticket flows, plus package configuration
  and initializers).

Non-code assets in the knowledge-bot tree (datasets, static admin files, bot
configuration JSON) are path-sensitive and will be reported honestly rather
than assumed movable.
