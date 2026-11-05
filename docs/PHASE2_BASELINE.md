# BASELINE — Backend Roadmap Phase 2 (single logging setup, no silent errors)

**Date:** 2026-11-05 · Recorded BEFORE any Phase 2 code change, on the code that
already contains the Phase 0+1 error-finding core (request-id, central error
handlers, error reporter).
**Environment (identical for every number below and for the Gate re-runs):**
- Python: `~/.fcenv/bin/python` (3.12, fastapi 0.115.0). Heavy ML deps
  (`sentence_transformers`, `faiss`) are **not installed on this machine**, so
  3 test files fail collection and FAQ/RAG-dependent tests fail. The Gate rule
  is **like-for-like**: same commands, same env, zero new failures vs this file.
- The live database lives on the owner's machine. (A small git-ignored
  `faq.db` file sits at the working-copy root, created by earlier local test
  runs; boot/drill work never uses it.) All boot/drill work uses throwaway
  DBs under `/tmp` only, with a harness that stubs ONLY the two missing ML
  modules in the harness process (no product code changed for it), and
  `SEND2_POLLER_ENABLED=0`.

## (a) Backend test suite

Command: `cd backend && ~/.fcenv/bin/python -m pytest . -q --continue-on-collection-errors`

**Result: 185 passed · 64 failed · 8 errors · 3 subtests passed**
(identical to the Phase 1 after-state; the full failed/error test-name set was
captured with this baseline and the Gate diffs against it name by name.)

Failures/errors by file (all pre-existing):

| File | Failed | Errors |
|---|---|---|
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
| test_audit_extended.py | | 1 (collection: ML deps) |
| test_destination_isolation_and_rag.py | | 1 (collection: ML deps) |
| test_faq_tenant_isolation.py | | 1 (collection: ML deps) |

## (b) Frontend

Commands in `frontend/`: `npx tsc --noEmit` and `npm test` (vitest).

- TypeScript: **12 errors** (4 in `src/components/onboarding/onboarding-kit-widget.tsx`,
  8 in `src/components/onboarding/onboarding-review-modal.tsx`) — known, pre-existing.
- Vitest: **37 passed / 5 failed** (42 tests). The 5 failures, all in
  `src/test/pages/views.test.tsx`: "uploads page > lists uploaded files" and 4
  "pending IT info pages > … shows the coming-next notice" (portfolio,
  technologies, careers, benefits) — known, pre-existing.

## (c) Logging configuration sites

`logging.basicConfig` call sites in non-test Python files: **6**

| File | Kind |
|---|---|
| `backend/main.py` | live app entry point |
| `backend/bifurcate.py` | CLI script (inside its `__main__` block) |
| `backend/extract.py` | CLI script (module level) |
| `backend/faq/service.py` | library module (configures logging on import — to be removed) |
| `routing/main.py` | legacy standalone entry point |
| `kb/main.py` | legacy standalone entry point |

## (d) Silent and unlogged error handlers — AST count (baseline of record)

Method: every `.py` file outside test files is parsed with the `ast` module
(files are BOM-tolerant decoded). For every `except` handler:
- **silent** = the handler body consists only of `pass` statements (a trailing
  source comment does not change this);
- **unlogged** = the handler body contains no logging call at all (no
  `logger.*` / `logging.*` call with a log-level method, no `log_*` / `*_log`
  helper call, no error-reporter call). Handlers that deliberately return a
  fallback or re-raise still count as unlogged — this is why this count (589)
  is higher than the earlier manual audit's ~418: the definitions differ, and
  this AST definition is the baseline of record for the Gate.

Results:

| Metric | Count |
|---|---|
| `except` handlers total (non-test, parseable files) | 1,341 |
| **Silent** (`except` … only `pass`) | **178** in 66 files |
| **Unlogged** (no logging call in body) | **589** in 131 files |

Worst files by silent count: `kb/services/whatsapp.py` 16 ·
`backend/database.py` 11 · `backend/routes/admin.py` 10 ·
`bots/customer_b2c/workflows.py` 8 · `backend/main.py` 7 ·
`backend/services/auto_bifurcator.py` 6 · `kb/services/brain.py` 6 ·
`kb/services/kb_handler.py` 6 · `backend/faq/service.py` 5 ·
`backend/services/offerings_flow.py` 5 · `kb/services/rag.py` 5.

One file cannot be parsed at all: `backend/services/universal_file_bifurcator.py`
begins with a literal `*** Begin File: … ***` marker line, i.e. it is not valid
Python today (a leftover patch artifact; its only import is lazy, inside a
function). It holds no countable handlers and is **out of scope** for this
logging-only phase — recorded here so the gap is explicit, not silent.

## (e) Hot-path file set (the live message flow, traced from the app entry)

The live app mounts the routing webhook router and starts the inbound
listener/poller; one inbound message travels: webhook entry → message
processing/orchestration → flow runner/state → WhatsApp senders, with dedup
checks and database writes throughout; tenant uploads travel the
bifurcator/ingest chain. Files classified **hot path** (50 files, holding
**83 of the 178** silent sites):

- **Webhook entry / routing chain:** `routing/webhook.py` (2), `routing/message.py` (2),
  `routing/classifier.py` (0), `routing/button_actions.py` (3), `routing/button_registry.py` (1),
  `routing/complaint_flow.py` (0), `routing/database.py` (0)
- **Orchestration / flow / state:** `backend/main.py` (7), `backend/services/orchestrator.py` (2),
  `backend/services/tenant_orchestrator.py` (0), `backend/services/flow_runner.py` (2),
  `backend/services/state_manager.py` (2), `backend/services/unified_bot.py` (2)
- **Dedup / listener / poller:** `backend/services/dedup.py` (0),
  `backend/services/incoming_listener.py` (2), `backend/db/webhooks.py` (0)
- **WhatsApp senders:** `routing/whatsapp.py` (2), `backend/services/whatsapp_sender.py` (1),
  `backend/services/whatsapp_outbox.py` (0), `backend/services/send_log.py` (0)
- **Uploads / bifurcator / ingest:** `backend/routes/tenant_files.py` (3),
  `backend/routes/pdf_intake.py` (1), `backend/services/auto_bifurcator.py` (6),
  `backend/services/upload_auto_router.py` (0), `backend/services/universal_pdf_bifurcator.py` (4),
  `backend/services/bifurcation_guards.py` (3), `backend/services/bifurcation_extract.py` (1),
  `backend/services/bifurcation_apply.py` (0), `backend/services/bifurcation_store.py` (0),
  `backend/services/bifurcation_validation.py` (0), `backend/services/pdf_intake.py` (1),
  `backend/services/pdf_product_extractor.py` (2), `backend/bifurcate.py` (2),
  `backend/faq/service.py` (5)
- **Database writes:** `backend/database.py` (11), `backend/database_multi_tenant.py` (0),
  and all of `backend/db/`: `admins.py` (1), `audit.py` (2), `chat.py` (1), `core.py` (2),
  `menu.py` (1), `products.py` (3), `sessions.py` (1), `settings.py` (4), `state.py` (1),
  `config_drafts.py` (0), `crawl.py` (0), `gaps.py` (0), `search.py` (0),
  `templates.py` (0), `webhooks.py` (0), `__init__.py` (0)

`print()` used as logging in sampled hot-path files: 7 calls, all in
`routing/button_actions.py`; every other sampled hot-path file has none.
