# Ops Runbook — final-chatbot

Post-deploy tracking for WhatsApp chatbot / multi-tenant platform.  
Full feature map: [`final-chatbot-implementation-inventory.md`](final-chatbot-implementation-inventory.md).  
Incident/DB review: [`final-chatbot-full-review-2026-11-01.md`](final-chatbot-full-review-2026-11-01.md).

---

## Canonical process

- **Run only** `backend/main.py` (unified FastAPI on port 9000).
- **Ignore** `updated_files_Shreyash/` (dead duplicate tree) and `backend/database_multi_tenant.py` (legacy INTEGER tenants — never initialized).
- **One server process** against one SQLite file. Two processes = double WhatsApp replies (each keeps its own in-memory dedup).
- Confirm at startup: log line `[STARTUP] DB paths app=… routing=… same=True`.

---

## Env that matters for WhatsApp

| Variable | Default / note |
|---|---|
| `SEND2_USERNAME` / `SEND2_PASSWORD` | Send2 auth |
| `SEND2_NUMBER` | Shared business WhatsApp number (poller routing) |
| `SEND2_POLLER_ENABLED` | `1` = poller answers (Meta webhook defers) |
| `ROUTING_DB_PATH` | Must be the same `faq.db` as the app |
| `DEFAULT_TENANT_ID` | Fallback tenant slug |
| `HANDOFF_TIMEOUT_NOTIFY` | **`0` default** — handoff timer clears state silently; set `1` to text “I’m back to help you” |
| Active Test Tenant | Admin console override — wins over phone binding on the shared number |

Tenant create now **requires** `mobile_number` (digits, unique). Meta bind stays on `POST …/phone-id`. Set/fix mobile via `POST …/mobile-number`.

---

## Background jobs (what can text customers)

| Job | Interval | Sends WhatsApp? |
|---|---|---|
| Send2 inbound poller | ~5 s | Yes — main reply path (`reason=reply`) |
| Handoff timeout | 60 s | Only if `HANDOFF_TIMEOUT_NOTIFY=1` (`reason=handoff_timeout`) |
| Cart/checkout cleanup | 1 h | No |
| Website crawl scheduler | 30 min | No |
| Campaigns / inbox / Active Test menu | Admin action | Yes (`campaign` / `inbox` / `active_test_menu`) |

---

## Symptom → where to look

| Symptom | Log tags / greps | Tables / API |
|---|---|---|
| Message with no customer trigger | `HANDOFF_TIMEOUT`, `ACTIVE_TEST_TENANT_MENU_PUSHED`, `null_menu` | `whatsapp_send_log` (`reason`), `user_states.human_handover` |
| Bot answered twice / re-answered old Hi | `DEDUP_FAIL_CLOSED`, `IncomingListener` | `processed_inbound_messages`, `webhook_dedup`, `chat_history` duplicates; count server processes |
| Empty inbound menu blast | `Skipping empty inbound` / `interactive null-text` | send_log `null_menu` only when interactive hint present |
| Wrong tenant answered | `tenancy.resolver`, `POLL_UNROUTED` | `tenants.mobile_number` / `waba_phone_id`, Active Test Tenant |
| Track one mobile end-to-end | — | `GET /api/admin/tenants/{tenant_id}/customer-360/{wa_id}` |
| Who sent what outbound | — | `whatsapp_send_log` (`reason`, `snippet`, `trigger_message_id`) |
| Webhook traffic for a number | — | `webhook_logs` (`wa_id`, `tenant_id`, `message_id` + payload hash) |

### Useful SQL

```sql
-- Why did this number get WhatsApp texts?
SELECT created_at, reason, tenant_id, snippet
FROM whatsapp_send_log
WHERE wa_id = '9198XXXXXXXX'
ORDER BY created_at DESC LIMIT 50;

-- Handoff timer candidates
SELECT wa_id, tenant_id, updated_at FROM user_states
WHERE human_handover = 1
  AND updated_at < datetime('now', '-3600 seconds');
```

---

## Deploy checklist

1. Single process; single `faq.db` (`ROUTING_DB_PATH` = app DB).
2. `SEND2_*` set; poller on/off intentional.
3. `HANDOFF_TIMEOUT_NOTIFY` left `0` unless product wants auto-text.
4. Each live tenant has real `mobile_number` (not `UNSET-…`).
5. Active Test Tenant cleared (or set deliberately) before production traffic.
6. LLM backend (`OLLAMA_*` / Groq / etc.) reachable.
7. After deploy: hit `/api/…` health/status; send one test WhatsApp; confirm `customer-360` shows chat + `whatsapp_send_log` reason=`reply`.

---

## Honesty gaps (do not debug as if live)

- BlueDart / Xpressbees **AWB booking is simulated** (random AWB); track may soft-fallback.
- Console roles are `super_admin` / `admin` / `sub_admin` only — not “Support Agent” / “Distributor” login roles (distributor is a data entity).
- `whatsapp_outbox` drain is not started from lifespan (retries not automatic).
- Knowledge often lives under `clients/<tenant>/` files, not only DB tables.
