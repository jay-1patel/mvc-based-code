# Error Hunting — find any failure in under a minute

This is the runbook for "something broke — where, and why?" It assumes the
Phase 1 error-finding core (request ids, central error handlers, error
reporter) and the Phase 2 logging rules (single setup, no silent errors).

## The 1-minute drill

1. **Get the request id.** When an API call fails, the browser console line
   and the error toast show `(error id: req_xxxxxxxx)`. The id is also in the
   `X-Request-Id` response header and in the JSON error body (`request_id`).
   No id? Use the failing API's path + the time it happened.
2. **Search the server log for that id.** One search finds the whole story:
   - `REQ req_xxxxxxxx | POST /path → 500 | 12ms | tenant=…` — which API,
     which status, how long it took (one line is written for **every**
     request; ≥500 lines are at ERROR level).
   - `API_FAIL | req=req_xxxxxxxx | api=POST /path | … | ValueError: …` —
     the exception summary for an unhandled 500 (full traceback follows at
     DEBUG level).
   - Any other log line emitted while that request was being handled ends
     with `| req=req_xxxxxxxx` — the suffix is attached automatically.
3. **Check the durable error store.** Unhandled API errors are also saved
   (provider `api`) and listed by the admin errors endpoint:
   `GET /api/admin/errors` (filter with `?provider=`, open one row with
   `GET /api/admin/errors/<id>`). The row carries the same request id, the
   API, the tenant, the HTTP status and the traceback — it survives log
   rotation and server restarts.
4. **For WhatsApp-flow failures** (no HTTP request involved), search by the
   customer's number or by TAG (below). Send-path and dedup failures carry
   `wa=` / `tenant=` fields; the routing layer also writes `webhook_logs`
   rows with wa_id / tenant_id / message_id filled in.

## Log format and TAGs

One format everywhere, configured in exactly one place (see below):

```
2026-11-05 07:39:32,122 [ERROR] router: REQ req_abcd1234 | POST /classify → 500 | 2ms | EXC JSONDecodeError | req=req_abcd1234
```

Every significant log line **starts its message with a searchable TAG** —
uppercase snake case, followed by ` | ` and `key=value` fields. Grep the TAG
to find every occurrence of that failure class. Key TAGs:

| TAG | Meaning |
|---|---|
| `REQ` | one line per API request (id, method, path, status, ms, tenant) |
| `API_FAIL` | unhandled API exception (central handler) |
| `API_ERROR` | error persisted by the error reporter (provider, api, severity, http status) |
| `ERROR_REPORT_FAILED` | the reporter itself failed while saving an API error |
| `DEDUP_FAIL_CLOSED` | dedup check failed — message treated as duplicate/skipped, never delivered twice |
| `SEND_LOG_RECORD_FAILED` | the WhatsApp send-ledger write failed (the send itself already happened) |
| `SESSION_OUTBOUND_UPDATE_FAILED` | post-send session bookkeeping write failed |
| `SET_TENANT_FAILED`, `SET_REQUEST_TENANT_FAILED` | tenant stamping failed — possible cross-tenant risk, investigate first |
| `UNANSWERED_QUERY_RECORD_FAILED` | recording an unanswered customer query failed |
| `AUDIT_EVENT_RECORD_FAILED`, `AUDIT_MUTATION_RECORD_FAILED` | an audit-trail write failed |
| `*_SKIPPED`, `*_DEFAULTED`, `*_FALLBACK` | a deliberate, commented ignore fired (DEBUG level) — see below |

## The no-silent-except rule

**No `except` block may swallow an error silently.** Every handler does one
of:

- **Real failure → log the exception** with a TAG:
  `logger.exception("SOME_TAG | key=%s", value)` at ERROR level (or the
  module's established warning pattern).
- **Deliberate ignore → DEBUG log + a comment.** Some ignores are correct by
  design (optional cleanup, resolver chains that fall through to the next
  source, parse-attempt chains, cosmetic enhancements). Those log at DEBUG
  with a TAG ending in `_SKIPPED` / `_DEFAULTED` / `_FALLBACK` **and** carry a
  `# deliberate: <why>` comment in the code, so a reader can tell intent from
  accident without guessing.

Historical note: the codebase once had 178 silent `except: pass` sites; they
were all converted under this rule (the count is part of the Phase 2 baseline
record and is re-checked by grep/AST counts, not by eyeballing).

## The single logging setup

- **One module configures logging:** `backend/core/logging.py`
  (`setup_logging()`). It sets the shared format on the root logger and
  attaches the request-context filter (the `| req=…` suffix) and the PII
  masking filter (phone numbers and emails are masked in log output).
- **Entry points call `setup_logging()` once**, as early as possible. That is
  the only place logging configuration happens — there are no other
  `basicConfig` calls in application code.
- **Library modules never configure logging.** They only do
  `logger = logging.getLogger(__name__)` (or import the shared logger their
  package already uses) and emit lines.
- The request id itself lives in `backend/core/context.py`
  (`request_id_var`) — the app middleware sets it per request and the
  logging filter reads it; there is exactly one such variable.

## If the trail is cold

- The failing line is a 4xx, not a 5xx: 4xx responses carry `request_id` in
  the body too — same drill, but there is no `API_FAIL` line (nothing
  crashed); the `REQ` line plus the response `detail` is the story.
- Validation (422) responses: the body is the framework default; the id is
  in the `X-Request-Id` header and on the `REQ` line.
- Nothing in the log at all: check you are looking at the process that
  actually serves the app, and that the request reached it (the `REQ` line
  is written for every request the app sees).
