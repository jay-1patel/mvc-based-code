# BASELINE — Backend Roadmap Phase 0 (recorded BEFORE any Phase 1 code change)

**Date:** 2026-11-05 · Recorded on the untouched base code, before any Phase 1 change
**Environment used for every number below (the Gate re-runs use the identical setup):**
- Python: `~/.fcenv/bin/python` (3.12, fastapi 0.115.0, PyJWT, bcrypt, slowapi, httpx present).
  Heavy ML deps (`sentence_transformers`, `faiss`) are **not installed on this VM** — this is why
  the counts below differ from the Step-1 run reported earlier (206 passed / 58 failed / 5 errors,
  taken in an environment that had them). 3 test files fail collection for that reason and
  FAQ/RAG-dependent tests fail. The Gate rule is **like-for-like**: same commands, same env,
  zero new failures vs this file.
- `faq.db`: **absent from the clone** (checked: no `*.db` in the repo; the live DB is on the
  user's own machine). All boot/drill work below used throwaway DBs under `/tmp` only.
- Boot/drill server: real app via a test harness that stubs ONLY the two missing ML modules
  (`sentence_transformers`, `faiss`) in the harness process — no project code changed —
  with `SEND2_POLLER_ENABLED=0` and `ROUTING_DB_PATH` pointed at the throwaway DB.

## (a) Backend test suite

Command: `cd backend && ~/.fcenv/bin/python -m pytest . -q --continue-on-collection-errors`

**Result: 180 passed · 64 failed · 8 errors · 3 subtests passed** (249 tests collected, 3 collection errors, 37.7s)

Failures/errors by file (all pre-existing in the base code):

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

WhatsApp flow coverage inside the suite: `test_menu_flow_features.py` passes fully;
`test_menu_button_dedupe.py` (2) and `test_whatsapp_routing_dedup.py` (3) failures are pre-existing above.

## (b) Frontend

Deps installed with `npm ci` (node_modules was absent). Commands run in `frontend/`:

- `npx tsc --noEmit` → **12 errors** (exit 2): 4 in `src/components/onboarding/onboarding-kit-widget.tsx`,
  8 in `src/components/onboarding/onboarding-review-modal.tsx`. (The known onboarding errors.)
- `npm test` (vitest) → **37 passed / 5 failed** (42 tests, 3 files: 1 failed / 2 passed).
  The 5 failures, all in `src/test/pages/views.test.tsx`: "uploads page > lists uploaded files" and
  4 "pending IT info pages > … shows the coming-next notice" (portfolio, technologies, careers, +1).

## (c) Boot test (throwaway DB)

- Server started on port 9911, throwaway DB `/tmp/fcbase/test.db`, poller OFF.
- `GET /docs` → **200** · `GET /` → **200** `{"status":"Unified Bot Service running"}`
- Live API count from the app: `app.routes` = **303** entries, of which **292 APIRoutes**;
  OpenAPI: 251 paths / **292 operations**. **Baseline live API count = 292.**

## (d) BEFORE error drill — `POST /classify` with invalid JSON

Client received (no request id anywhere):

```
HTTP/1.1 500 Internal Server Error
content-type: text/plain; charset=utf-8
content-length: 21

Internal Server Error
```

Server log showed only the Uvicorn line + a raw ASGI traceback:

```
INFO:     127.0.0.1:52634 - "POST /classify HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
  + Exception Group Traceback (most recent call last): …
```

Grep of the whole server log for `X-Request-Id` / `request_id` / app `REQ` lines: **0**.
Finding this error today = reading a ~110-line traceback and matching by time. Phase 1 must beat this.
