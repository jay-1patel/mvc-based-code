# final-chatbot — Implementation Inventory & Error-Tracking Guide

**Purpose (for the senior's requirement):** a complete list of everything implemented in this codebase — features, APIs, database tables, background jobs, integrations — and, for each area, **where to look when something breaks after deployment**. Prepared 1 Nov 2026 from the full repo at commit `5c69d7d` (286 Python files, ~94,000 lines of Python, React frontend, 12 client packs in `clients/`).

**How the system runs:** one unified FastAPI service (`backend/main.py`, port 9000) mounts **33 routers** and starts the background jobs in §3. Around it: `routing/` (WhatsApp webhook + send2 sending), `kb/` (bot brain: RAG, tools, WhatsApp service layer), `bots/` (B2C customer flows + B2B distributor flows), `shared/` (tenancy, validators, tenancy profile loading), `frontend/` (React admin console), `eval/` (offline evaluation harness), `scripts/` (utilities).

---

## 1. Feature inventory — everything that is implemented

### 1.1 Inbound message channels
| Implemented thing | Where | Notes |
|---|---|---|
| Meta WhatsApp webhook receiver | `routing/webhook.py`, `backend/routes/tenant_webhook.py`, `send2_webhook.py` | Defers to the poller when the poller is enabled |
| send2.digital inbound poller | `backend/services/incoming_listener.py` | Polls every 5 s over a 15-min window; dedup claim before processing |
| Webhook deduplication (2 layers + content fingerprint) | `backend/services/dedup.py`, tables `webhook_dedup`, `processed_inbound_messages` | Fail-open on DB errors — see known issues |
| Tenant resolution for inbound traffic | `shared/tenancy/resolver.py` | Order: Active Test Tenant override → phone binding (`tenants.waba_phone_id`) → per-user stamp. Unrouted messages are dropped with one warning log |
| Opt-out listener / campaign opt-outs | table `campaign_opt_outs`, webhook handling | STOP-style handling |

### 1.2 Conversation engine
| Implemented thing | Where |
|---|---|
| Message orchestrator (single entry for all channels) | `backend/services/orchestrator.py` |
| Unified bot (customer context, routing between flows) | `backend/services/unified_bot.py` |
| Conversation state machine | `user_states` table (state + context JSON + language), `state_manager` logger |
| B2C customer flows: menus, catalogue, cart, checkout | `bots/customer_b2c/` (menus.py, cart.py, workflows.py, tools.py) |
| B2B distributor flows: catalogue, ordering, finance, support tickets | `bots/distributor_b2b/` (catalog_flow, order_flow, finance_flow, ticket_flow) |
| FAQ/RAG answering (FAISS + embeddings + Ollama LLM) | `kb/services/rag.py`, `backend/faq/`, tables `faq_dataset`, `knowledge_base`, `cached_embeddings` |
| LLM tools the bot can call (10) | `kb/services/tools.py`: `search_products`, `get_product_details`, `get_nutrition_info`, `check_order_status`, `get_faq_answer`, `get_contact_details`, `human_handover`, `list_categories`, `get_products_by_category`, `calculate_margin` |
| Human handover + agent inbox (assign / resolve / reply / attach) | `backend/routes/inbox.py`, `conversations.py`, table `handoffs` |
| Unanswered-question capture for later review | table `unanswered_queries` (tenant + wa_id + confidence) |
| Multi-language state, response formatting, emoji/markdown controls | `user_states.lang`, formatter services + env switches |

### 1.3 Tenant platform
| Implemented thing | Where |
|---|---|
| Tenant registration (super-admin direct; others via approval queue) | `backend/routes/tenants.py`, `tenant_approvals.py`, tables `tenants`, `tenant_change_requests` + events |
| Tenant profile: draft → publish → version history → rollback | `draft_config`, `published_config`, `publish_history`, `tenant_profile_versions` |
| Phone-number binding (one number ↔ one tenant, anti-steal) | `POST /api/tenants/{id}/phone-id`, `shared/tenancy/store.py` |
| Per-tenant webhook secret + API tokens (hashed) | `tenants.webhook_secret`, `tenant_tokens` |
| Active Test Tenant (console picks which tenant the shared number answers as) | `app_settings`, tenants routes |
| Auto-onboarding from a website (scrape → offerings/FAQs) + scheduled re-crawls | `backend/services/url_scraper.py`, `tenant_crawl_schedules` |
| File onboarding: upload → extract → bifurcate into KB/products/FAQs | `backend/routes/offerings.py`, `tenant_files.py`, `pdf_intake.py`, services `extract`, `bifurcate` — ⚠ one bifurcator file is corrupt (known blocker) |
| Onboarding kits (example packs) | `backend/routes/onboarding_kits.py`, `onboarding_kit_examples/` |
| API-access onboarding requests (payment/order APIs, approval flow) | `api_onboarding_requests` + events + entitlements |
| Client packs (12 prebuilt tenant configs) | `clients/`: leewaysoftech, meridianbank, sbi, ltfinance, dlf, apexlogistics, troogood, wanderly, northwindcapital, skylinerealty, 99destinations, … |

### 1.4 Commerce
| Implemented thing | Where |
|---|---|
| Product catalogue + categories + offerings management | `backend/routes/catalog.py`, `offerings.py`, tables `products`, `menus`, `menu_settings` |
| Cart + checkout sessions with TTL cleanup | `bots/customer_b2c/cart.py`, tables `carts`, `cart_items`, `checkout_sessions` |
| Orders (+ idempotency key, per-user history index) | `backend/routes/orders.py`, table `orders` |
| Payments: confirmations, per-tenant payment records | tables `tenant_payments`, `payment_confirmations`, `invoice_requests`; Razorpay + Stripe integration code |
| Shipments / tracking | table `tenant_shipments`; BlueDart + XpressBees integration code |
| Serviceable-pincode check | env prefix list + `serviceable_pincodes` table (⚠ table only via never-run migration) |
| Distributors management | `backend/routes/distributors.py`, table `distributors` |

### 1.5 CRM, campaigns, support
| Implemented thing | Where |
|---|---|
| Customer directory (auto-created on first message) | table `customers`, `touch_customer` in `backend/database.py` |
| Conversation browser + per-customer thread + chat history | `backend/routes/conversations.py` |
| Leads CRM (status pipeline, assignment, phone index) | `backend/routes/leads.py`, table `leads` |
| Complaints / tickets | `backend/routes/complaints.py`, table `complaints` |
| Callback scheduling (agents, availability, Google Meet configs, summaries) | `backend/routes/callbacks.py`, `callback_*` tables (⚠ several callback tables are dead — §6) |
| Campaigns: create, segment, send via templates, replies, opt-outs | `backend/routes/campaigns.py`, tables `campaigns`, `campaign_replies`, `segments`, `whatsapp_templates` |
| WhatsApp template registration on send2 | `kb/services/template_manager.py` |
| Send retry outbox | table `whatsapp_outbox` (⚠ migration-only table; drain function has no caller) |

### 1.6 Admin, auth, safety
| Implemented thing | Where |
|---|---|
| Admin accounts, roles, permissions, OTP login, recovery key | `backend/routes/auth.py`, tables `admins`, `admin_otps` |
| Admin audit trail (who did what, per tenant) | tables `admin_audit_events`, `audit_logs`; `backend/routes/audit.py`, `audit_logs.py` |
| Dynamic configuration (runtime settings without redeploy) | `backend/routes/dynamic_config.py`, `app_settings` |
| Chat admin (send as agent, user list) | `backend/routes/chat_admin.py`, `admin_chat_messages` |
| Test simulator (fake conversations against a tenant) | `backend/routes/test_simulator.py` |
| Health/status endpoints | `backend/routes/status.py` |
| Rate limiting, webhook signature/secret checks | services `rate_limiter`, `tenancy.signing`, env `SEND2_WEBHOOK_SECRET`, `WHATSAPP_VERIFY_TOKEN` |

---

## 2. API surface

~320 live route definitions. Biggest routers: `admin.py` (40), `tenants.py` (27), `campaigns.py` (21), `offerings.py` (20), `callbacks.py` (15), `conversations.py` (14), `tenant_admin.py` (14), `catalog.py` (13), `auth.py` (12), `tenant_files.py` (11), `unified.py` (10). **Full route list: Appendix A.**

## 3. Background jobs (things that run — and send — on their own)

| Job | Where started | Interval | What it does | Can it message customers? |
|---|---|---|---|---|
| send2 inbound poller | `backend/main.py` lifespan | 5 s | Fetches inbound messages, runs the bot, sends replies | **Yes — it's the main reply path** |
| Handoff timeout monitor | `backend/main.py` lifespan | 60 s | Reverts handovers idle > 1 h | **Yes — sends the "team hasn't responded yet, I'm back" message itself** |
| Cart/checkout cleanup | `backend/main.py` lifespan | 1 h | Deletes expired carts (7 d) and checkouts (15 min) | No |
| Website re-crawl scheduler | `backend/main.py` lifespan | 30 min | Re-scrapes tenant sites per `tenant_crawl_schedules` | No (changes KB content) |
| Outbox drain | — | — | Defined (`whatsapp_outbox.py`) but **nothing calls it** | Would retry sends if it ran |
| Cron cleanup script | `backend/cron/cleanup.py` | manual/cron only | Dead-letters old outbox rows, trims logs | No |

## 4. Database inventory

80 table definitions exist in code; a fresh `init_db()` builds **68**. Full list with defining file: **Appendix B**. The ones that matter daily:

| Table | Holds | Keyed by |
|---|---|---|
| `tenants` | Tenant registry (slug, vertical, status, phone binding, webhook secret, domain) | `id` (TEXT slug) |
| `customers` | Customer directory, profile, message counts, first/last seen | `(tenant_id, wa_id)` UNIQUE |
| `chat_history` | Every exchange: customer message + bot response + route | `wa_id` + `tenant_id` stamp |
| `user_sessions` / `user_states` | Live session + conversation state machine | `wa_id` **only — no tenant** ⚠ |
| `webhook_logs` | Traffic log: direction, endpoint, status, timing — **payload hash only; no wa_id/tenant** ⚠ | — |
| `webhook_dedup` / `processed_inbound_messages` | Duplicate-answer protection ledgers | `message_id` |
| `products`, `orders`, `carts`, `checkout_sessions` | Commerce data | orders: `wa_id`; **no tenant_id** ⚠ |
| `leads`, `handoffs`, `unanswered_queries`, `complaints` | CRM/support records | `tenant_id` + `wa_id` (complaints: wa_id only) |
| `campaigns`, `campaign_replies`, `campaign_opt_outs`, `segments` | Broadcast machinery | `tenant_id` on campaigns |
| `faq_dataset`, `knowledge_base`, `cached_embeddings` | RAG knowledge + vectors | `tenant_id` (faq/kb) |
| `admins`, `admin_otps`, `admin_audit_events`, `audit_logs` | Admin auth + audit trail | — |
| `draft_config`, `published_config`, `tenant_profile_versions` | Tenant profile draft/publish/versioning | `tenant_id` |
| `tenant_crawl_schedules`, `tenant_integrations`, `tenant_tokens`, `tenant_shipments`, `tenant_payments` | Per-tenant operations | `tenant_id` |
| `app_settings` | Runtime settings incl. Active Test Tenant | key |

Knowledge also lives **outside** the DB: tenant packs and scraped content under `clients/<tenant>/` files — on your current data, most bot knowledge is in files, not tables.

## 5. Integrations

| Service | Used for | Config |
|---|---|---|
| send2.digital | WhatsApp sending + inbound reports (primary channel) | `SEND2_*` env (username/password, number, per-endpoint URLs) |
| Meta WhatsApp (WABA) | Webhook channel, phone-id bindings | `WHATSAPP_VERIFY_TOKEN`, tenant `waba_phone_id`/`webhook_secret` |
| Hookdeck | Webhook relay | `HKDEK_WEBHOOK_URL` |
| Ollama (local) | LLM answers + embeddings | `OLLAMA_*`, `EMBEDDING_MODEL_PATH` |
| Groq / Mistral / Grok (xAI) | Alternate LLM providers | `GROQ_*`, `MISTRAL_*`, `GROK_API_KEY` |
| imghippo + catbox.moe | Public hosting so WhatsApp can fetch media | `IMGHIPPO_*`, `CATBOX_API_URL`, `PUBLIC_BASE_URL` |
| Google | OAuth connect, Meet for callbacks, service account | `GOOGLE_*` |
| Razorpay / Stripe | Payments | integration modules + tenant payment records |
| BlueDart / XpressBees | Shipment tracking | integration modules |
| SMTP | Email notifications | `SMTP_*` |
| Tesseract | OCR on uploaded images/PDFs | `TESSERACT_CMD` |

Configuration: **~120 environment variables** (full list: Appendix C). The load-bearing ones: `SEND2_NUMBER` (the shared WhatsApp number), `SEND2_POLLER_ENABLED`, `DEFAULT_TENANT_ID`, `PUBLIC_BASE_URL`, `ROUTING_DB_PATH` (one of five DB-path resolvers — they must all point at the same `faq.db`).

## 6. Implemented but dead / misleading (know these before debugging)

- `backend/database_multi_tenant.py` — a second, incompatible tenants schema (`whatsapp_number`, INTEGER ids). Never initialised; do not debug against it.
- `updated_files_Shreyash/` — a full duplicate copy of backend+routing inside the repo. Not executed.
- `whatsapp_outbox` drain — no caller; retries don't happen (and the table itself is migration-only).
- `tenant_kb` / `tenant_faq` — written only when `AUTO_BIFURCATE=1`; live retrieval never reads them.
- Callback cluster tables (`callback_analytics`, `callback_meeting_history`, `google_meet_configs`, `recurring_callbacks`) — created every startup, essentially unused.
- `send_sticker` — intentionally disabled no-op. `kb/main.py` and `routing/main.py` — standalone entry points beside the unified app; know which one a deployment actually runs.

## 7. How to track an error after deployment (the tracking guide)

**Logging today:** console logging via `logging.basicConfig` in each entry point (`backend/main.py` sets the main format). There is **no log file, rotation, or central collection configured in code** — where logs live depends on how the process is run (terminal, systemd journal, Docker logs). Named loggers act as topic tags; important events also use ALL-CAPS prefixes (`TENANT_CREATED |`, `HANDOFF_TIMEOUT |`, `DEDUP_FAIL_CLOSED |`). **Audit tables** (`admin_audit_events`, `audit_logs`) record admin actions permanently; `webhook_logs` records traffic metadata (+ `wa_id` / `tenant_id` / `message_id`); **`whatsapp_send_log`** records every outbound reason.

**Ops runbook:** see [`OPS_RUNBOOK.md`](OPS_RUNBOOK.md) (canonical process, deploy checklist, SQL greps).

**Symptom → where to look:**

| Symptom | Loggers / log tags | Tables / endpoints |
|---|---|---|
| Bot answered twice, or answered an old message | `IncomingListener`, `dedup` (`DEDUP_FAIL_CLOSED`) | `processed_inbound_messages`, `webhook_dedup`, `chat_history` (duplicate rows); one server process only |
| Bot messaged with no trigger at all | `handoff_timeout` (`HANDOFF_TIMEOUT \| notify=0\|1`), Active Test push | `whatsapp_send_log.reason` (`handoff_timeout` / `active_test_menu` / `null_menu`); `HANDOFF_TIMEOUT_NOTIFY` (default off) |
| Customer message never answered | `tenancy.resolver`, `IncomingListener` (`POLL_UNROUTED`) | `unanswered_queries`; tenant `mobile_number` + Active Test Tenant |
| Wrong tenant answered | `tenancy.resolver`, `tenancy.store` | `tenants.mobile_number`, `tenants.waba_phone_id`, Active Test Tenant |
| Track everything from one mobile | — | `GET /api/admin/tenants/{tenant_id}/customer-360/{wa_id}` |
| Upload / file ingest failed | `offerings`, `extract`, `auto_bifurcator`, `pdf_intake` | `admin_files` |
| Menu/catalogue wrong or stale | `menu_service`, `tenant_menu_service`, `url_scraper` | `menus`, `menu_settings`, `tenant_crawl_schedules`, `clients/<tenant>/` |
| Order / payment problem | `orders`, `order_service`, `payment_checkout` | `orders`, `tenant_payments`, `payment_confirmations` |
| Campaign didn't go / wrong audience | `campaigns` | `campaigns`, `campaign_replies`, `whatsapp_send_log` (`reason=campaign`) |
| Customer stuck with the bot / agent never got it | `inbox`, `handoff_timeout` | `handoffs`, `user_states.human_handover`, `/inbox` |
| Admin action dispute ("who changed this?") | — | `admin_audit_events`, `audit_logs`, `publish_history` |

**Remaining blind spots:** heavy `except Exception` swallowing; no Sentry/OTel yet; correlation is primarily `message_id` + `whatsapp_send_log` / `webhook_logs` columns rather than a distributed trace id.

---

*Generated from code at commit `5c69d7d`, 1 Nov 2026. Appendices A–C are machine-extracted from the same code.*

---

## Appendix A — Full live route list (machine-extracted)


**backend/main.py** (6)

- `GET /`
- `GET /admin`
- `GET /admin/{full_path:path}`
- `GET /chat-history`
- `POST /classify`
- `POST /forward`

**backend/routes/admin.py** (40)

- `DELETE /api/admin/brochures/{brochure_id}`
- `DELETE /api/admin/files/{filename}`
- `DELETE /api/admin/logs`
- `DELETE /api/admin/menus/{menu_key}/{item_id}`
- `DELETE /api/admin/products/{product_id}`
- `GET /api/admin/analytics/frequent-questions`
- `GET /api/admin/analytics/overview`
- `GET /api/admin/analytics/response-time`
- `GET /api/admin/analytics/top-questions`
- `GET /api/admin/analytics/unanswered`
- `GET /api/admin/brochures`
- `GET /api/admin/brochures/relevant`
- `GET /api/admin/brochures/{brochure_id}`
- `GET /api/admin/chat-history`
- `GET /api/admin/customers`
- `GET /api/admin/dashboard/stats`
- `GET /api/admin/download-remote`
- `GET /api/admin/files`
- `GET /api/admin/files/{filename}/download`
- `GET /api/admin/files/{filename}/preview`
- `GET /api/admin/logs`
- `GET /api/admin/menus`
- `GET /api/admin/menus/{menu_key}`
- `GET /api/admin/menus/{menu_key}/settings`
- `GET /api/admin/products`
- `POST /api/admin/analytics/unanswered/convert`
- `POST /api/admin/brochures/extract-metadata`
- `POST /api/admin/brochures/upload`
- `POST /api/admin/files/upload`
- `POST /api/admin/menus/{menu_key}/items`
- `POST /api/admin/menus/{menu_key}/reorder`
- `POST /api/admin/menus/{menu_key}/reset`
- `POST /api/admin/products`
- `POST /api/admin/rebuild-index`
- `POST /api/admin/upload/catbox`
- `POST /api/admin/upload/image`
- `POST /api/admin/upload/logo`
- `PUT /api/admin/menus/{menu_key}/settings`
- `PUT /api/admin/menus/{menu_key}/{item_id}`
- `PUT /api/admin/products/{product_id}`

**backend/routes/api_onboarding.py** (5)

- `GET /api/admin/integration-requests`
- `GET /api/admin/integration-requests/{request_id}/events`
- `GET /api/integration-requests`
- `POST /api/admin/integration-requests/{request_id}/decision`
- `POST /api/integration-requests`

**backend/routes/audit.py** (4)

- `GET /api/admin/audit-export`
- `GET /api/admin/audit-history`
- `GET /api/admin/audit-history/{event_id}`
- `GET /api/admin/audit-statistics`

**backend/routes/audit_logs.py** (3)

- `GET /api/audit-logs`
- `GET /api/audit-logs/export`
- `GET /api/audit-logs/statistics`

**backend/routes/auth.py** (12)

- `DELETE /api/auth/admins/{username}`
- `GET /api/auth/admins`
- `GET /api/auth/me`
- `GET /api/auth/status`
- `PATCH /api/auth/admins/{username}`
- `POST /api/auth/admins/{username}/reset-password`
- `POST /api/auth/change-password`
- `POST /api/auth/create`
- `POST /api/auth/first-admin`
- `POST /api/auth/login`
- `POST /api/auth/request-otp`
- `POST /api/auth/reset-password`

**backend/routes/callbacks.py** (15)

- `GET /api/tenants/{tenant_id}/agents/{agent_id}/availability`
- `GET /api/tenants/{tenant_id}/callbacks`
- `GET /api/tenants/{tenant_id}/callbacks/stats`
- `GET /api/tenants/{tenant_id}/callbacks/time-slots`
- `GET /api/tenants/{tenant_id}/callbacks/upcoming`
- `GET /api/tenants/{tenant_id}/callbacks/{callback_id}`
- `GET /api/tenants/{tenant_id}/callbacks/{callback_id}/summary`
- `POST /api/tenants/{tenant_id}/agents/{agent_id}/availability`
- `POST /api/tenants/{tenant_id}/callbacks`
- `POST /api/tenants/{tenant_id}/callbacks/meet-test`
- `POST /api/tenants/{tenant_id}/callbacks/{callback_id}/cancel`
- `POST /api/tenants/{tenant_id}/callbacks/{callback_id}/complete`
- `POST /api/tenants/{tenant_id}/callbacks/{callback_id}/reschedule`
- `POST /api/tenants/{tenant_id}/callbacks/{callback_id}/schedule`
- `PUT /api/tenants/{tenant_id}/callbacks/{callback_id}`

**backend/routes/campaigns.py** (21)

- `DELETE /api/admin/tenants/{tenant_id}/campaigns/segments/{segment_id}`
- `DELETE /api/admin/tenants/{tenant_id}/campaigns/templates/{template_id}`
- `DELETE /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}`
- `GET /api/admin/tenants/{tenant_id}/campaigns`
- `GET /api/admin/tenants/{tenant_id}/campaigns/recipients`
- `GET /api/admin/tenants/{tenant_id}/campaigns/segments`
- `GET /api/admin/tenants/{tenant_id}/campaigns/templates`
- `GET /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/metrics`
- `GET /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/replies`
- `POST /api/admin/tenants/{tenant_id}/campaigns`
- `POST /api/admin/tenants/{tenant_id}/campaigns/audience/count`
- `POST /api/admin/tenants/{tenant_id}/campaigns/media`
- `POST /api/admin/tenants/{tenant_id}/campaigns/segments`
- `POST /api/admin/tenants/{tenant_id}/campaigns/templates`
- `POST /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/duplicate`
- `POST /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/send`
- `POST /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/send-selection`
- `POST /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}/test-send`
- `PUT /api/admin/tenants/{tenant_id}/campaigns/segments/{segment_id}`
- `PUT /api/admin/tenants/{tenant_id}/campaigns/templates/{template_id}`
- `PUT /api/admin/tenants/{tenant_id}/campaigns/{campaign_id}`

**backend/routes/catalog.py** (13)

- `DELETE /catalog/new-arrivals/delete/{product_id}`
- `DELETE /catalog/products/delete/{product_id}`
- `GET /catalog/categories`
- `GET /catalog/new-arrivals/list`
- `GET /catalog/products/by-category/{category}`
- `GET /catalog/products/by-slug/{slug}`
- `GET /catalog/products/catalog`
- `GET /catalog/products/get/{product_id}`
- `GET /catalog/products/list`
- `GET /catalog/products/search`
- `POST /catalog/new-arrivals/add`
- `POST /catalog/products/add`
- `PUT /catalog/products/update/{product_id}`

**backend/routes/chat.py** (2)

- `GET /api/chat/history/{wa_id}`
- `POST /api/chat/send`

**backend/routes/chat_admin.py** (4)

- `DELETE /api/admin/chat/message/{msg_id}`
- `GET /api/admin/chat/users`
- `POST /api/admin/chat/send`
- `WEBSOCKET /api/admin/chat/ws/{username}`

**backend/routes/complaints.py** (6)

- `DELETE /api/complaints/{ticket_id}`
- `GET /api/complaints`
- `GET /api/complaints/{ticket_id}`
- `POST /api/complaints/{ticket_id}/reply`
- `POST /api/complaints/{ticket_id}/resolve`
- `PUT /api/complaints/{ticket_id}`

**backend/routes/conversations.py** (14)

- `DELETE /api/admin/tenants/{tenant_id}/complaints/{ticket_id}`
- `GET /api/admin/tenants/{tenant_id}/agents`
- `GET /api/admin/tenants/{tenant_id}/chat-history`
- `GET /api/admin/tenants/{tenant_id}/complaints`
- `GET /api/admin/tenants/{tenant_id}/conversations`
- `GET /api/admin/tenants/{tenant_id}/conversations/{wa_id}`
- `GET /api/admin/tenants/{tenant_id}/inbox`
- `POST /api/admin/tenants/{tenant_id}/complaints/{ticket_id}/reply`
- `POST /api/admin/tenants/{tenant_id}/inbox/assign`
- `POST /api/admin/tenants/{tenant_id}/inbox/attach`
- `POST /api/admin/tenants/{tenant_id}/inbox/resolve/{wa_id}`
- `POST /api/admin/tenants/{tenant_id}/inbox/{wa_id}/reply`
- `PUT /api/admin/tenants/{tenant_id}/complaints/{ticket_id}`
- `PUT /api/admin/tenants/{tenant_id}/inbox/handover/{wa_id}`

**backend/routes/distributors.py** (6)

- `DELETE /api/admin/tenants/{tenant_id}/distributors/{wa_id}`
- `GET /api/admin/tenants/{tenant_id}/distributors`
- `GET /api/admin/tenants/{tenant_id}/distributors/count`
- `GET /api/admin/tenants/{tenant_id}/distributors/lookup/{wa_id}`
- `POST /api/admin/tenants/{tenant_id}/distributors`
- `PUT /api/admin/tenants/{tenant_id}/distributors/{wa_id}`

**backend/routes/dynamic_config.py** (6)

- `GET /api/admin/dynamic/products/feed`
- `GET /api/admin/dynamic/{scope}`
- `GET /api/admin/dynamic/{scope}/history`
- `POST /api/admin/dynamic/{scope}/build-draft`
- `POST /api/admin/dynamic/{scope}/publish`
- `PUT /api/admin/dynamic/{scope}`

**backend/routes/faqs.py** (1)

- `GET /faqs`

**backend/routes/google_connect.py** (5)

- `GET /api/tenants/google-connect/callback`
- `GET /api/tenants/{tenant_id}/google-connect/callback`
- `GET /api/tenants/{tenant_id}/google-connect/oauth-url`
- `GET /api/tenants/{tenant_id}/google-connect/status`
- `POST /api/tenants/{tenant_id}/google-connect/disconnect`

**backend/routes/inbox.py** (8)

- `DELETE /api/inbox/conversation/{wa_id}`
- `GET /api/inbox/agents`
- `GET /api/inbox/queue`
- `POST /api/inbox/assign`
- `POST /api/inbox/reset-bot-state/{wa_id}`
- `POST /api/inbox/resolve/{wa_id}`
- `PUT /api/inbox/agents/status`
- `PUT /api/inbox/handover/{wa_id}`

**backend/routes/integrations.py** (7)

- `GET /api/tenants/{tenant_id}/integrations`
- `GET /api/tenants/{tenant_id}/shipping/track/{awb}`
- `POST /api/tenants/{tenant_id}/integrations/{provider}/config`
- `POST /api/tenants/{tenant_id}/integrations/{provider}/test`
- `POST /api/tenants/{tenant_id}/payments/create-session`
- `POST /api/tenants/{tenant_id}/shipping/book-shipment`
- `POST /api/webhooks/payments/{tenant_id}/{provider}`

**backend/routes/leads.py** (4)

- `GET /api/leads`
- `GET /api/leads/export.csv`
- `GET /api/leads/{lead_id}`
- `PUT /api/leads/{lead_id}`

**backend/routes/offerings.py** (20)

- `DELETE /api/admin/tenants/{tenant_id}/offerings/staged`
- `DELETE /api/admin/tenants/{tenant_id}/offerings/{offering_id}`
- `DELETE /api/admin/tenants/{tenant_id}/products/staged`
- `DELETE /api/admin/tenants/{tenant_id}/record-schema/columns/{key}`
- `GET /api/admin/tenants/{tenant_id}/offerings`
- `GET /api/admin/tenants/{tenant_id}/offerings/categories`
- `GET /api/admin/tenants/{tenant_id}/offerings/staged`
- `GET /api/admin/tenants/{tenant_id}/products/staged`
- `GET /api/admin/tenants/{tenant_id}/record-schema`
- `POST /api/admin/tenants/{tenant_id}/ingest-file`
- `POST /api/admin/tenants/{tenant_id}/offerings`
- `POST /api/admin/tenants/{tenant_id}/offerings/approve-staged`
- `POST /api/admin/tenants/{tenant_id}/offerings/ingest-pdf`
- `POST /api/admin/tenants/{tenant_id}/offerings/upload-pdf`
- `POST /api/admin/tenants/{tenant_id}/products/approve-staged`
- `POST /api/admin/tenants/{tenant_id}/products/upload-pdf`
- `POST /api/admin/tenants/{tenant_id}/record-schema/columns`
- `POST /api/admin/tenants/{tenant_id}/record-schema/reset`
- `PUT /api/admin/tenants/{tenant_id}/offerings/{offering_id}`
- `PUT /api/admin/tenants/{tenant_id}/record-schema/columns/{key}`

**backend/routes/onboarding_kits.py** (5)

- `GET /api/admin/onboarding-kits/list`
- `GET /api/admin/onboarding-kits/{vertical}/{fmt}`
- `POST /api/admin/onboarding/stage`
- `POST /api/admin/tenants/{tenant_id}/onboarding/publish`
- `POST /api/admin/tenants/{tenant_id}/onboarding/stage`

**backend/routes/orders.py** (5)

- `DELETE /api/orders/{order_id}`
- `GET /api/orders`
- `GET /api/orders/stats`
- `GET /api/orders/{order_id}`
- `PUT /api/orders/{order_id}`

**backend/routes/pdf_intake.py** (3)

- `GET /api/admin/tenants/{tenant_id}/pdf-intake/placements`
- `POST /api/admin/tenants/{tenant_id}/pdf-intake/preview`
- `POST /api/admin/tenants/{tenant_id}/pdf-intake/publish`

**backend/routes/send2_webhook.py** (1)

- `POST /api/send2/webhook`

**backend/routes/status.py** (5)

- `DELETE /webhook/logs`
- `GET /webhook/health`
- `GET /webhook/logs`
- `GET /webhook/logs/stats`
- `GET /webhook/status`

**backend/routes/tenant_admin.py** (14)

- `DELETE /api/admin/tenants/{tenant_id}`
- `GET /api/admin/tenants/list`
- `GET /api/admin/tenants/{tenant_id}`
- `GET /api/admin/tenants/{tenant_id}/kb`
- `GET /api/admin/tenants/{tenant_id}/menus`
- `GET /api/admin/tenants/{tenant_id}/menus/{menu_type}`
- `POST /api/admin/tenants/register`
- `POST /api/admin/tenants/{tenant_id}/deactivate`
- `POST /api/admin/tenants/{tenant_id}/kb/upload`
- `POST /api/admin/tenants/{tenant_id}/kb/upload-batch`
- `POST /api/admin/tenants/{tenant_id}/menus`
- `POST /api/admin/tenants/{tenant_id}/menus/preview`
- `POST /api/admin/tenants/{tenant_id}/onboard`
- `PUT /api/admin/tenants/{tenant_id}`

**backend/routes/tenant_approvals.py** (5)

- `GET /api/admin/tenant-change-requests`
- `GET /api/admin/tenant-change-requests/{request_id}/events`
- `GET /api/tenant-change-requests`
- `POST /api/admin/tenant-change-requests/{request_id}/decision`
- `POST /api/tenant-change-requests`

**backend/routes/tenant_chat.py** (5)

- `DELETE /api/admin/tenants/{tenant_id}/chat/message/{msg_id}`
- `GET /api/admin/tenants/{tenant_id}/chat/users`
- `POST /api/admin/tenants/{tenant_id}/chat/attach`
- `POST /api/admin/tenants/{tenant_id}/chat/send`
- `WEBSOCKET /api/admin/tenants/{tenant_id}/chat/ws/{username}`

**backend/routes/tenant_files.py** (11)

- `DELETE /api/admin/tenants/{tenant_id}/files/{filename}`
- `GET /api/admin/tenants/{tenant_id}/files`
- `GET /api/admin/tenants/{tenant_id}/files/{filename}/download`
- `GET /api/admin/tenants/{tenant_id}/files/{filename}/preview`
- `GET /api/admin/tenants/{tenant_id}/knowledge-gaps`
- `POST /api/admin/tenants/{tenant_id}/files/auto-bifurcate`
- `POST /api/admin/tenants/{tenant_id}/files/bifurcate-existing`
- `POST /api/admin/tenants/{tenant_id}/files/image`
- `POST /api/admin/tenants/{tenant_id}/files/upload`
- `POST /api/admin/tenants/{tenant_id}/knowledge-gaps/{gap_id}/create-faq`
- `POST /api/admin/tenants/{tenant_id}/knowledge-gaps/{gap_id}/resolve`

**backend/routes/tenant_operations.py** (7)

- `DELETE /api/admin/tenants/{tenant_id}/orders/{order_id}`
- `GET /api/admin/tenants/{tenant_id}/customers`
- `GET /api/admin/tenants/{tenant_id}/customers/export`
- `GET /api/admin/tenants/{tenant_id}/orders`
- `GET /api/admin/tenants/{tenant_id}/orders/stats`
- `GET /api/admin/tenants/{tenant_id}/orders/{order_id}`
- `PUT /api/admin/tenants/{tenant_id}/orders/{order_id}`

**backend/routes/tenant_webhook.py** (7)

- `GET /api/tenant/tenants`
- `GET /api/tenant/tenants/{tenant_id}/config`
- `POST /api/tenant/message`
- `POST /api/tenant/middleware/webhook`
- `POST /api/tenant/tenants/{tenant_id}/message`
- `POST /api/tenant/webhook`
- `POST /api/tenant/{tenant_slug}/webhook`

**backend/routes/tenants.py** (27)

- `DELETE /api/admin/tenants/{tenant_id}`
- `DELETE /api/admin/tenants/{tenant_id}/crawl-schedule`
- `DELETE /api/admin/tenants/{tenant_id}/tokens/{token_id}`
- `GET /api/admin/tenants`
- `GET /api/admin/tenants/active-test-tenant`
- `GET /api/admin/tenants/{tenant_id}/crawl-schedule`
- `GET /api/admin/tenants/{tenant_id}/detail`
- `GET /api/admin/tenants/{tenant_id}/resolved`
- `GET /api/admin/tenants/{tenant_id}/smoke`
- `GET /api/admin/tenants/{tenant_id}/tokens`
- `GET /api/admin/tenants/{tenant_id}/versions`
- `GET /api/admin/tenants/{tenant_id}/versions/{version}`
- `POST /api/admin/tenants`
- `POST /api/admin/tenants/active-test-tenant`
- `POST /api/admin/tenants/auto-onboard-execute`
- `POST /api/admin/tenants/auto-onboard-preview`
- `POST /api/admin/tenants/{tenant_id}/crawl-schedule/run`
- `POST /api/admin/tenants/{tenant_id}/phone-id`
- `POST /api/admin/tenants/{tenant_id}/publish`
- `POST /api/admin/tenants/{tenant_id}/rollback`
- `POST /api/admin/tenants/{tenant_id}/scrape-website`
- `POST /api/admin/tenants/{tenant_id}/test-question`
- `POST /api/admin/tenants/{tenant_id}/tokens`
- `PUT /api/admin/tenants/{tenant_id}/crawl-schedule`
- `PUT /api/admin/tenants/{tenant_id}/intents/{intent_name}`
- `PUT /api/admin/tenants/{tenant_id}/profile`
- `PUT /api/admin/tenants/{tenant_id}/webhook-secret`

**backend/routes/unified.py** (10)

- `GET /api/unified/analytics`
- `GET /api/unified/analytics/per-bot`
- `GET /api/unified/bots`
- `GET /api/unified/bots/{bot_type}`
- `GET /api/unified/context/{wa_id}`
- `GET /api/unified/export/chat-history`
- `GET /api/unified/health`
- `POST /api/unified/batch-context`
- `POST /api/unified/chat`
- `POST /api/unified/route`

**kb/main.py** (12)

- `GET /`
- `GET /admin`
- `GET /admin-panel`
- `GET /admin-panel/`
- `GET /admin/`
- `GET /admin/{path:path}`
- `GET /rate-limiter-stats`
- `POST /send-interactive`
- `POST /send-media`
- `POST /send-menu`
- `POST /send-message`
- `POST /send-product`

**routing/main.py** (2)

- `GET /`
- `GET /chat-history`

**routing/webhook.py** (2)

- `GET `
- `POST `

---

## Appendix B — All table definitions in code (machine-extracted)

- `admin_audit_events` — created by startup init (database.py / backend/db) · defined in: backend/db/audit.py, updated_files_Shreyash/backend/database.py
- `admin_chat_messages` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `admin_files` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `admin_otps` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `admins` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_audit.py, backend/test_audit_extended.py, updated_files_Shreyash/backend/database.py
- `agent_availability` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/routes/callbacks.py, updated_files_Shreyash/backend/database.py
- `agent_status` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `api_onboarding_entitlements` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_api_onboarding.py, updated_files_Shreyash/backend/database.py
- `api_onboarding_request_events` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_api_onboarding.py, updated_files_Shreyash/backend/database.py
- `api_onboarding_requests` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_api_onboarding.py, updated_files_Shreyash/backend/database.py
- `app_settings` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/db/settings.py, updated_files_Shreyash/backend/database.py
- `audit_logs` — created by startup init (database.py / backend/db) · defined in: backend/db/audit.py, updated_files_Shreyash/backend/database.py
- `audit_test_mutations` — test files only · defined in: backend/test_audit.py
- `button_clicks` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `cached_embeddings` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callback_agents` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callback_analytics` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callback_meeting_history` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callback_notifications` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callback_summaries` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/routes/callbacks.py, updated_files_Shreyash/backend/database.py
- `callback_types` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `callbacks` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/routes/callbacks.py, updated_files_Shreyash/backend/database.py
- `campaign_opt_outs` — created by startup init (database.py / backend/db) · defined in: backend/database.py, routing/webhook.py, updated_files_Shreyash/backend/database.py, updated_files_Shreyash/routing/webhook.py
- `campaign_replies` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `campaigns` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `cart_items` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/migrations/run_b2c_migration.py, updated_files_Shreyash/backend/database.py
- `carts` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/migrations/run_b2c_migration.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `chat_history` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_destination_isolation_and_rag.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `checkout_sessions` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/migrations/run_b2c_migration.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `complaints` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `conversation_assignments` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `customers` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `deleted_chat_messages` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `distributors` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `draft_config` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_audit.py, updated_files_Shreyash/backend/database.py
- `faq_dataset` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `google_meet_configs` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `handoffs` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `incoming_messages` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `invoice_requests` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `knowledge_base` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `leads` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `menu_settings` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `menus` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `orders` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `payment_confirmations` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `processed_inbound_messages` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `products` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `products_new` — created by startup init (database.py / backend/db) · defined in: backend/database.py
- `publish_history` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_audit.py, updated_files_Shreyash/backend/database.py
- `published_config` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_audit.py, updated_files_Shreyash/backend/database.py
- `recurring_callbacks` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `segments` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `serviceable_pincodes` — migration script only — NOT created at startup · defined in: backend/migrations/run_b2c_migration.py
- `session_buttons` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_change_request_events` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_change_requests` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_crawl_schedules` — created by startup init (database.py / backend/db) · defined in: backend/database.py
- `tenant_faq` — only via bifurcate.py (AUTO_BIFURCATE path) · defined in: backend/bifurcate.py
- `tenant_integrations` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_kb` — only via bifurcate.py (AUTO_BIFURCATE path) · defined in: backend/bifurcate.py
- `tenant_menu_configs` — dead module only (database_multi_tenant.py) · defined in: backend/database_multi_tenant.py
- `tenant_menu_items` — dead module only (database_multi_tenant.py) · defined in: backend/database_multi_tenant.py
- `tenant_payments` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_profile_versions` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_audit.py, updated_files_Shreyash/backend/database.py
- `tenant_record_columns` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_shipments` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `tenant_tokens` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_audit.py, updated_files_Shreyash/backend/database.py
- `tenants` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/database_multi_tenant.py, backend/test_api_onboarding.py, backend/test_audit.py, backend/test_tenant_audit.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `unanswered_queries` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `user_consents` — migration script only — NOT created at startup · defined in: backend/migrations/run_b2c_migration.py
- `user_sessions` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `user_states` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/test_tenant_operations.py, updated_files_Shreyash/backend/database.py
- `webhook_dedup` — created by startup init (database.py / backend/db) · defined in: backend/database.py, backend/migrations/run_b2c_migration.py, updated_files_Shreyash/backend/database.py
- `webhook_logs` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py
- `whatsapp_outbox` — migration script only — NOT created at startup · defined in: backend/migrations/run_b2c_migration.py
- `whatsapp_templates` — created by startup init (database.py / backend/db) · defined in: backend/database.py, updated_files_Shreyash/backend/database.py

---

## Appendix C — All environment variables read by the code (machine-extracted)

`ADMIN_API_KEY`, `ADMIN_JWT_SECRET`, `ADMIN_NOTIFICATION_MODE`, `ADMIN_RECOVERY_KEY`, `ADMIN_SECRET_KEY`, `ADMIN_WHATSAPP_ID`, `ALERT_WEBHOOK_URL`, `ALLOWED_ORIGINS`, `API_PROVIDER`, `AUTO_BIFURCATE`, `AUTO_BIFURCATE_FULL`, `AUTO_DETECT_URLS`, `BIFURCATE_DB`, `BIFURCATE_OLLAMA_TIMEOUT`, `BIFURCATE_USE_LLM`, `BOT_NAME`, `BRAND_LOGO_EMOJI`, `BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_WEBSITE`, `BUTTON_REGISTRY_FILE`, `CATALOGUE_BODY_TEXT`, `CATALOGUE_BUTTON_ID`, `CATALOGUE_BUTTON_TITLE`, `CATALOGUE_PDF_FILENAME`, `CATALOGUE_PDF_URL`, `CATALOG_DELIVERY_MODE`, `CATBOX_API_URL`, `CHUNK_OVERLAP`, `CHUNK_SIZE`, `COMPANY_TYPE`, `CORS_ORIGINS`, `DEFAULT_TENANT_ID`, `DOMAIN`, `EMBEDDING_MODEL_PATH`, `EMOJI_ENABLED`, `EMOJI_FREQUENCY`, `EXTRACTED_DIR`, `FORCE_HTTPS`, `FORMATTING_LEVEL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GROK_API_KEY`, `GROQ_API_KEY`, `GROQ_API_URL`, `GROQ_MODEL`, `HKDEK_WEBHOOK_URL`, `HYPERLINKS_ENABLED`, `IMAGE_BASE_URL`, `IMGHIPPO_API_KEY`, `IMGHIPPO_API_URL`, `INDUSTRY`, `LLM_BATCH_SIZE`, `LLM_TIMEOUT`, `MARKDOWN_ENABLED`, `MENU_BODY`, `MENU_FOOTER`, `MENU_HEADER`, `MISTRAL_API_BACKUP`, `MISTRAL_API_KEY`, `MISTRAL_API_URL`, `MISTRAL_MODEL`, `OLLAMA_API_KEY`, `OLLAMA_API_URL`, `OLLAMA_BASE_URL`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`, `OLLAMA_URL`, `PUBLIC_BASE_URL`, `QA_ADMIN_PASS`, `QA_ADMIN_USER`, `QA_BASE_URL`, `ROUTING_DB_PATH`, `SCRAPER_JS_RENDER`, `SEND2_API_KEY`, `SEND2_API_SECRET`, `SEND2_BASE_URL`, `SEND2_ENVIRONMENT`, `SEND2_INCOMING_ENTRY_URL`, `SEND2_INCOMING_URL`, `SEND2_LIST_MENU_URL`, `SEND2_MEDIA_URL`, `SEND2_NUMBER`, `SEND2_PASSWORD`, `SEND2_PHONE_NUMBER_ID`, `SEND2_POLLER_ENABLED`, `SEND2_POLL_INTERVAL_SECONDS`, `SEND2_POLL_MAX_BACKOFF_SECONDS`, `SEND2_POLL_TIMEOUT_SECONDS`, `SEND2_QUICK_BUTTON_URL`, `SEND2_RATE_LIMIT`, `SEND2_RETRIES`, `SEND2_SESSION_MSG_URL`, `SEND2_SESSION_SEND_URL`, `SEND2_TEMPLATE_URL`, `SEND2_TIMEOUT`, `SEND2_USERNAME`, `SEND2_WEBHOOK_SECRET`, `SEND2_WHA_BUSINESS_ID`, `SEND_MEDIA`, `SERVICEABLE_PINCODE_PREFIXES`, `SIGNATURE`, `SMTP_FROM_EMAIL`, `SMTP_HOST`, `SMTP_PASSWORD`, `SMTP_USERNAME`, `SUPPORT_EMAIL`, `SUPPORT_PHONE`, `TEMPLATE_PREFIX`, `TESSERACT_CMD`, `TESTING`, `TRUSTED_PROXY_IPS`, `UPLOAD_DIR`, `WELCOME_MESSAGE`, `WHATSAPP_FORMATTING`, `WHATSAPP_VERIFY_TOKEN`


Loggers (108): `IncomingListener`, `My Business_bot`, `OptoutListener`, `alerts`, `auto_bifurcator`, `b2b_catalog_flow`, `b2b_finance_flow`, `b2b_order_flow`, `b2b_ticket_flow`, `b2b_tools`, `b2b_workflows`, `b2c_cart`, `b2c_menus`, `b2c_migration`, `b2c_tools`, `b2c_workflows`, `bifurcate`, `bluedart_integration`, `brochure_service`, `campaigns`, `cart_cleanup`, `cart_service`, `chat`, `chiki_webhook`, `company_api`, `complaints`, `compliance`, `conversations`, `cron`, `custom_integration`, `database`, `database_multi_tenant`, `dedup`, `distributors`, `dynamic_config`, `dynamic_router`, `extract`, `faq_bot`, `flow_runner`, `google_connect`, `google_meet_service`, `handoff_timeout`, `hookdeck`, `httpcore`, `httpx`, `inbox`, `integrations_router`, `json_history`, `leads`, `llm`, `media_host`, `menu_catalog`, `menu_handler`, `menu_service`, `message`, `offerings`, `offerings_flow`, `onboarding_kits`, `orchestrator`, `order_service`, `orders`, `outbox`, `package_flow`, `payment_checkout`, `pdf_bifurcator`, `pdf_intake`, `pdf_intake_route`, `pdf_product_extractor`, `pdf_products`, `pdf_section_extractor`, `rate_limiter`, `razorpay_integration`, `records`, `response_formatters`, `response_formatters_middleware`, `router`, `send2_webhook`, `services.intent_answers`, `services.service_answers`, `state_manager`, `stripe_integration`, `tenancy.defaults`, `tenancy.gating`, `tenancy.intent_match`, `tenancy.loader`, `tenancy.resolver`, `tenancy.signing`, `tenancy.store`, `tenant-approvals`, `tenant_admin`, `tenant_chat`, `tenant_files`, `tenant_menu_service`, `tenant_operations`, `tenant_orchestrator`, `tenant_rag`, `tenant_webhook`, `tenants`, `test_simulator`, `unified_api`, `unified_bot`, `unified_retrieval`, `upload_auto_router`, `url_scraper`, `validators`, `whatsapp_formatter`, `whatsapp_sender`, `xpressbees_integration`
