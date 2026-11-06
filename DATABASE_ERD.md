# Database ERD — Chiki Unified Chatbot Platform

> SQLite · 54 tables · Generated 2026-11-01

---

## How to read this document

- **PK** = Primary Key  **FK** = Foreign Key (SQLite does not enforce FKs by default — they are logical relationships)
- `tenant_id` is present on almost every table. It is always a TEXT FK → `tenants.id`.
- `wa_id` is a WhatsApp phone number string (e.g. `919876543210`). It is the customer identifier inside `customers`, `chat_history`, `user_states`, and every downstream table.
- Arrows mean: **table A → table B** = A has a column that references B's PK.

---

## Domain Map (high level)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PLATFORM CORE                                                               │
│  tenants  ──────────────────────────────────────────────────────────────┐   │
│  tenant_profile_versions                                                │   │
│  tenant_tokens                                                          │   │
│  tenant_change_requests / _events                                       │   │
│  app_settings / published_config / draft_config / publish_history       │   │
└─────────────────────────────────────────────────────────────────────────│───┘
                                                                          │
        every table below has tenant_id → tenants.id                      │
                                                                          ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐
│   MESSAGING  │  │   COMMERCE   │  │     CRM      │  │   SCHEDULING        │
│              │  │              │  │              │  │                     │
│ chat_history │  │ products     │  │ customers    │  │ callbacks           │
│ user_states  │  │ carts        │  │ leads        │  │ callback_agents     │
│ user_sessions│  │ cart_items   │  │ handoffs     │  │ agent_availability  │
│ webhook_logs │  │ checkout_    │  │ distributors │  │ callback_types      │
│ webhook_dedup│  │   sessions   │  │ complaints   │  │ recurring_callbacks │
│ processed_   │  │ orders       │  │ handoffs     │  │ callback_notific.   │
│   inbound    │  │ payment_     │  │              │  │ callback_summaries  │
│ whatsapp_    │  │   confirm.   │  │              │  │ callback_analytics  │
│   send_log   │  │ invoice_req. │  │              │  │ callback_meeting_   │
│ incoming_    │  │ campaigns    │  │              │  │   history           │
│   messages   │  │ campaign_opt │  │              │  │ google_meet_configs │
│ session_     │  │   _outs      │  │              │  │                     │
│   buttons    │  │ campaign_    │  │              │  └─────────────────────┘
│ button_clicks│  │   replies    │  │              │
│              │  │ segments     │  └──────────────┘
└──────────────┘  │ whatsapp_    │
                  │   templates  │  ┌──────────────────────────────────────┐
                  └──────────────┘  │  KNOWLEDGE / CONTENT                 │
                                    │  faq_dataset                         │
┌──────────────────────────────┐    │  knowledge_base                      │
│  ADMIN & AUDIT               │    │  cached_embeddings                   │
│  admins                      │    │  menus / menu_settings               │
│  admin_otps                  │    │  admin_files                         │
│  admin_chat_messages         │    │  tenant_crawl_schedules              │
│  deleted_chat_messages       │    └──────────────────────────────────────┘
│  agent_status                │
│  conversation_assignments    │    ┌──────────────────────────────────────┐
│  admin_audit_events          │    │  INTEGRATIONS                        │
│  audit_logs                  │    │  tenant_integrations                 │
│  unanswered_queries          │    │  tenant_payments                     │
│  tenant_record_columns       │    │  tenant_shipments                    │
│  api_onboarding_requests     │    │  api_onboarding_entitlements         │
│  api_onboarding_request_     │    └──────────────────────────────────────┘
│    events                    │
│  api_onboarding_entitlements │
└──────────────────────────────┘
```

---

## Full Table Relationships

### 1. PLATFORM CORE

```
tenants
  PK: id (TEXT)
  ├── slug (UNIQUE)
  ├── mobile_number (UNIQUE, partial — excludes UNSET-* placeholders)
  ├── waba_phone_id (UNIQUE where not null/empty)
  ├── vertical, status, display_name, domain
  └── created_at, updated_at

tenant_profile_versions
  PK: id
  FK: tenant_id → tenants.id
  ── one row per published profile version; is_current=1 marks the live one
  ── UNIQUE(tenant_id) WHERE is_current=1

tenant_tokens
  PK: id
  FK: tenant_id → tenants.id
  ── scoped API tokens minted per tenant; token_hash is the stored secret

tenant_change_requests
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── approval workflow for create_tenant / publish_profile
  ── status: pending → approved / rejected
  ── UNIQUE(request_type, tenant_id) WHERE status='pending'

tenant_change_request_events
  PK: id
  FK: request_id → tenant_change_requests.id ON DELETE CASCADE
  ── audit trail for each status transition

app_settings          key/value store (global settings, active_test_tenant, etc.)
published_config      scoped live config snapshots
draft_config          scoped in-progress config drafts
publish_history       immutable log of every publish action
```

---

### 2. MESSAGING

```
chat_history
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── wa_id + tenant_id → every message/response pair
  ── route: which bot answered (faq / kb / orchestrator / human)
  IDX: (tenant_id, created_at), (tenant_id, wa_id, created_at)

user_states
  PK: (tenant_id, wa_id)
  FK: tenant_id → tenants.id (logical)
  ── current bot state machine position for a customer
  ── human_handover=1 means agent is handling the conversation
  Columns: state, context_json, lang, version, human_handover, handover_resolved_at,
           active_flow, current_step, collected (JSON), status DEFAULT 'bot', updated_at
  IDX: tenant_id, wa_id

user_sessions
  PK: (tenant_id, wa_id)
  FK: tenant_id → tenants.id (logical)
  ── tracks open/close of 24h WhatsApp session window

processed_inbound_messages
  PK: message_id
  ── dedup layer — prevents re-processing the same inbound message
  ── wa_id, channel, processed_at

webhook_dedup
  PK: message_id
  ── secondary dedup (HTTP-layer); IDX: processed_at

webhook_logs
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── every inbound/outbound webhook hit; direction, status_code, payload_hash
  IDX: (wa_id, created_at), (tenant_id, message_id)

incoming_messages
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── raw polled rows from send2.digital before processing

whatsapp_send_log
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── every outbound WhatsApp send with reason code
  ── reason codes: reply / null_menu / active_test_menu / handoff_timeout /
  ──               campaign_blast / callback_notify / etc.
  IDX: (wa_id, created_at), (tenant_id, wa_id, created_at)

whatsapp_outbox
  PK: id
  ── retry queue for failed outbound sends
  ── status: pending → sent / failed
  ── attempts + next_retry implement exponential backoff (max 3 attempts)
  ── drained every 30s by outbox_drain_monitor background task
  ── created in init_db (database.py); NOT per-tenant — wa_id is the only key
  IDX: (status, next_retry)

session_buttons
  PK: id; UNIQUE: button_id
  ── ephemeral interactive buttons sent to a user (wa_id + session_id)
  ── expires_at used for TTL; triggered_at set on click

button_clicks
  PK: id
  FK: button_id → session_buttons.button_id (logical)
  ── every button press by wa_id

whatsapp_templates
  PK: id; UNIQUE: name
  ── global template registry; status: registered / pending / rejected

```

---

### 3. COMMERCE

```
products  (also called "offerings")
  PK: id
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (tenant_id, slug)
  ── full product catalog per tenant
  IDX: (tenant_id, is_active)

carts
  PK: id
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (tenant_id, wa_id)
  ── one active cart per customer per tenant; expires_at for TTL
  IDX: expires_at

cart_items
  PK: id
  FK: cart_id → carts.id ON DELETE CASCADE
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (cart_id, product_id)
  IDX: cart_id

checkout_sessions
  PK: id (UUID)
  FK: tenant_id → tenants.id (logical)
  ── multi-step checkout state machine; status: active/processing/confirmed/cancelled/expired
  ── cart_snapshot_json + order_draft_json capture the in-progress order
  IDX: (wa_id, status)

orders
  PK: id; UNIQUE: order_number
  FK: tenant_id → tenants.id (logical)
  ── placed orders; wa_id links to customer
  ── idempotency_key prevents double-submission
  IDX: (tenant_id, created_at), (wa_id, created_at), (tenant_id, wa_id)

payment_confirmations
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── manual UPI/bank payment confirmations submitted via WhatsApp
  IDX: (tenant_id, created_at)

invoice_requests
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── customer requests a formal invoice for an order_number

campaigns
  PK: id
  FK: tenant_id → tenants.id (logical)
  FK: segment_id → segments.id (logical)
  ── broadcast campaigns; schedule_mode: now / scheduled
  ── status lifecycle: draft → scheduled → sending → completed / failed

campaign_opt_outs
  PK: id
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (tenant_id, wa_id)
  ── customers who sent STOP; always excluded from campaign blasts

campaign_replies
  PK: id
  FK: campaign_id → campaigns.id ON DELETE CASCADE
  ── replies received from customers after a campaign send

segments
  PK: id
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (tenant_id, name)
  ── saved audience filters (distributor / customer criteria)

```

---

### 4. CRM

```
customers
  PK: id
  UNIQUE: (tenant_id, wa_id)
  FK: tenant_id → tenants.id (logical)
  ── master customer directory; created/updated on every inbound message
  ── phone may differ from wa_id if user volunteers a number in a flow
  IDX: (tenant_id, last_seen), phone

leads
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── sales leads collected from WhatsApp flows
  ── status: new → contacted → qualified → won / lost / closed
  IDX: (tenant_id, status, created_at), (tenant_id, phone)

handoffs
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── human handover requests; status: pending → claimed → resolved / expired
  IDX: (tenant_id, status, created_at), (wa_id, status)

complaints
  PK: id; UNIQUE: ticket_id
  FK: tenant_id → tenants.id (logical)
  ── customer complaints; status: open → resolved
  IDX: (tenant_id, wa_id)

distributors
  PK: wa_id
  FK: tenant_id → tenants.id (logical)
  ── B2B distributor directory; tier, region, sales_volume, outstanding_payments
  ── wa_id IS the PK here (not a separate integer id)

unanswered_queries
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── knowledge gaps: queries the bot couldn't answer confidently
  IDX: (tenant_id, resolved, created_at)

tenant_record_columns
  PK: id
  FK: tenant_id → tenants.id (logical)
  UNIQUE: (tenant_id, key)
  ── custom CRM fields per tenant

```

---

### 5. SCHEDULING (Callbacks)

```
callbacks
  PK: id (UUID)
  FK: tenant_id → tenants.id
  ── a single callback booking; status: pending → confirmed → completed / cancelled
  IDX: tenant_id, wa_id, status, scheduled_start_time, meet_link

callback_agents
  PK: id; UNIQUE: (tenant_id, agent_id)
  FK: tenant_id → tenants.id
  ── human agents who take callbacks

agent_availability
  PK: id
  FK: tenant_id → tenants.id
  FK: agent_id → callback_agents.agent_id
  FK: callback_id → callbacks.id (optional — set when slot is booked)
  UNIQUE: (tenant_id, agent_id, date, start_time, end_time)
  IDX: (tenant_id, agent_id, date, is_available), (agent_id, date, start_time)

callback_types
  PK: id
  FK: tenant_id → tenants.id
  UNIQUE: (tenant_id, type_name)
  ── named callback categories (e.g. "Sales Call", "Support")

recurring_callbacks
  PK: id
  FK: tenant_id → tenants.id
  ── recurring schedule templates

callback_notifications
  PK: id
  FK: callback_id → callbacks.id ON DELETE CASCADE
  FK: tenant_id → tenants.id
  ── reminder/notification rows; status: pending → sent / failed
  ⚠  WARNING: nothing currently polls and fires pending rows — not implemented
  IDX: (status, created_at)

callback_summaries
  PK: id
  FK: callback_id → callbacks.id ON DELETE CASCADE
  FK: tenant_id → tenants.id
  ── post-call notes, outcome, follow-up, rating

callback_analytics
  PK: id
  FK: tenant_id → tenants.id
  UNIQUE: (tenant_id, date, callback_type, status, agent_id)

callback_meeting_history
  PK: id
  FK: callback_id → callbacks.id ON DELETE CASCADE
  FK: tenant_id → tenants.id
  ── meeting recording URL, duration, transcript, quality score

google_meet_configs
  PK: id; UNIQUE: tenant_id
  FK: tenant_id → tenants.id
  ── per-tenant Google Calendar / Meet credentials
```

---

### 6. KNOWLEDGE / CONTENT

```
faq_dataset
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── FAQ chunks with embeddings; used by FAISS vector search
  IDX: tenant_id

knowledge_base
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── long-form KB articles with chunk JSON and embedding blob

cached_embeddings
  PK: chunk_index
  FK: tenant_id → tenants.id (logical)
  ── precomputed embeddings for RAG retrieval
  IDX: tenant_id

admin_files
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── uploaded files (PDFs, CSVs) that feed the KB; tracks bifurcation status
  IDX: (tenant_id, created_at)

menus
  PK: id; UNIQUE: (menu_key, item_id)
  ── WhatsApp list menu items per menu_key (not yet per-tenant — shared global)

menu_settings
  PK: menu_key
  ── header/body/footer/button_text for each menu

tenant_crawl_schedules
  PK: id; UNIQUE: tenant_id
  FK: tenant_id → tenants.id (logical)
  ── auto re-crawl config per tenant; next fire = last_crawled_at + interval_hours
  IDX: (is_enabled, last_crawled_at)
```

---

### 7. ADMIN & AUDIT

```
admins
  PK: id; UNIQUE: username
  FK: tenant_id → tenants.id (logical, NULL for super_admin)
  ── UNIQUE(role) WHERE role='super_admin' — only one super admin
  ── UNIQUE(tenant_id) WHERE role='admin' — one admin per tenant

admin_otps
  PK: id
  ── short-lived OTP codes for admin 2FA; username + otp_code + expires_at

admin_chat_messages
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── internal admin chat (not customer-facing)
  IDX: (tenant_id, created_at), chat_id, created_at

deleted_chat_messages
  PK: id; UNIQUE: (message_id, username)
  FK: message_id → admin_chat_messages.id (logical)
  IDX: message_id

agent_status
  PK: agent_id
  ── online/offline status of support agents

conversation_assignments
  PK: (tenant_id, wa_id)
  FK: tenant_id → tenants.id (logical)
  ── which agent_id is currently handling a customer conversation

admin_audit_events
  PK: id
  ── every admin API mutation; actor, action, resource, IP, user-agent
  IDX: created_at DESC, actor, action, tenant

audit_logs
  PK: id
  ── high-level structured audit events (actor, action, category, status)
  IDX: created_at, actor, action, category, status

api_onboarding_requests
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── requests to enable payment_api / order_api per tenant
  ── status: pending → approved / rejected
  UNIQUE(tenant_id, api_type) WHERE status IN ('pending','approved')
  IDX: (tenant_id, created_at DESC)

api_onboarding_request_events
  PK: id
  FK: request_id → api_onboarding_requests.id
  ── audit trail of each status change

api_onboarding_entitlements
  PK: (tenant_id, api_type)
  FK: request_id → api_onboarding_requests.id (UNIQUE)
  ── the actual grant row once approved
```

---

### 8. INTEGRATIONS

```
tenant_integrations
  PK: id; UNIQUE: (tenant_id, provider)
  FK: tenant_id → tenants.id (logical)
  ── per-tenant third-party credentials (payment / order providers)
  ── credentials_json stored encrypted; status: configured / verified / error
  IDX: tenant_id

tenant_payments
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── payment sessions created via integrated payment gateway
  ── status: created → pending → paid / failed / refunded
  IDX: (tenant_id, created_at DESC)

tenant_shipments
  PK: id
  FK: tenant_id → tenants.id (logical)
  ── shipments booked via integrated logistics provider
  ── status: booked → in_transit → out_for_delivery → delivered / returned / failed
  IDX: (tenant_id, awb_number)
```

---

## Traceability: given a mobile number, find everything

This is the single most important query path for debugging.

```
wa_id = '919876543210'   -- the customer's WhatsApp number
tenant_id = 'xyz-corp'  -- or search across all tenants

-- 1. Who is this customer?
SELECT * FROM customers WHERE wa_id = ? AND tenant_id = ?;

-- 2. Full conversation history
SELECT created_at, message, response, route
FROM chat_history WHERE wa_id = ? AND tenant_id = ? ORDER BY created_at;

-- 3. Every outbound message + WHY it was sent
SELECT created_at, reason, snippet
FROM whatsapp_send_log WHERE wa_id = ? AND tenant_id = ? ORDER BY created_at;

-- 4. Current bot state
SELECT state, human_handover, active_flow, current_step, updated_at
FROM user_states WHERE wa_id = ? AND tenant_id = ?;

-- 5. Session open/close times
SELECT last_inbound_at, last_outbound_at, session_open
FROM user_sessions WHERE wa_id = ? AND tenant_id = ?;

-- 6. Orders
SELECT order_number, status, total_amount, created_at
FROM orders WHERE wa_id = ? AND tenant_id = ? ORDER BY created_at;

-- 7. Leads
SELECT flow, status, collected, created_at FROM leads WHERE wa_id = ? AND tenant_id = ?;

-- 8. Complaints
SELECT ticket_id, complaint_type, status, created_at
FROM complaints WHERE wa_id = ? AND tenant_id = ?;

-- 9. Handoffs (human agent escalations)
SELECT reason, status, assignee, created_at FROM handoffs WHERE wa_id = ? AND tenant_id = ?;

-- 10. Callbacks booked
SELECT id, status, scheduled_start_time, meet_link, created_at
FROM callbacks WHERE wa_id = ? AND tenant_id = ?;

-- 11. Cart (active)
SELECT c.expires_at, ci.product_id, ci.qty, ci.price_at_add
FROM carts c JOIN cart_items ci ON ci.cart_id = c.id
WHERE c.wa_id = ? AND c.tenant_id = ?;

-- 12. Raw inbound dedup log (use to check if a message was processed)
SELECT * FROM processed_inbound_messages WHERE wa_id = ? ORDER BY processed_at DESC LIMIT 20;

-- 13. Webhook hits from this number
SELECT direction, status_code, created_at FROM webhook_logs
WHERE wa_id = ? AND tenant_id = ? ORDER BY created_at DESC;
```

API shortcut: `GET /api/admin/tenants/{tenant_id}/customer-360/{wa_id}` returns all of the above in one call.

---

## Known Gaps (schema level)

| Gap | Table affected | Impact |
|---|---|---|
| `callback_notifications` never sent | callback_notifications | Reminders sit as `pending` forever |
| `menus` not per-tenant | menus, menu_settings | All tenants share one menu structure |
| `admins.email` not UNIQUE | admins | Two admins can register the same email |
| `customers.phone` not validated vs `wa_id` | customers | Phone field often equals wa_id (meaningless) |
| Old tenants have `mobile_number = UNSET-{id}` | tenants | Can't look up tenant by real phone |
| No per-tenant WABA credentials | tenants | All tenants share one WhatsApp number |
| `whatsapp_templates` not scoped per tenant | whatsapp_templates | Template names must be globally unique |

---

## Background Tasks Reference

| Task | Interval | Sends WhatsApp? | Log prefix |
|---|---|---|---|
| `incoming_listener` | 5s | YES — replies to inbound | `[INBOUND_POLL]` |
| `outbox_drain_monitor` | 30s | YES — retries failed sends | `OUTBOX_DRAIN` |
| `handoff_timeout_monitor` | 60s | Only if `HANDOFF_TIMEOUT_NOTIFY=1` | `HANDOFF_TIMEOUT` |
| `campaign_scheduler_monitor` | 60s | YES — fires scheduled campaigns | `CAMPAIGN_SCHEDULER_FIRED` |
| `cart_cleanup_monitor` | 1h | NO | `CART_CLEANUP` |
| `crawl_scheduler_monitor` | 30m | NO | `CRAWL_SCHEDULE_DONE` |
| `_push_active_test_menu` | on admin action | Only if `ACTIVE_TEST_MENU_PUSH=1` | `ACTIVE_TEST_TENANT_MENU_PUSHED` |
