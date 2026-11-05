"""Schema creation and migration ladder — extracted from backend/database.py."""
import os
import sqlite3

from .core import logger, get_db_context, _ensure_columns
from .audit import _init_admin_audit_tables


# ── Schema ───────────────────────────────────────────────────────────────

def init_db():
    with get_db_context() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wa_id TEXT NOT NULL,
                sender_name TEXT,
                message TEXT NOT NULL,
                response TEXT,
                route TEXT DEFAULT 'faq',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        # Migrate older DBs that predate the route column
        chat_cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_history)")]
        if "route" not in chat_cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN route TEXT DEFAULT 'faq'")
            # Backfill from the "[route] message" prefix used by save_chat
            conn.execute(
                """UPDATE chat_history
                   SET route = LOWER(SUBSTR(message, 2, INSTR(message, ']') - 2))
                   WHERE message LIKE '[%]%' AND INSTR(message, ']') > 2"""
            )
        # Tenancy: which brand a conversation belongs to. The bot resolves it
        # from the inbound WABA phone-id (shared.tenancy.resolver) and stamps it
        # here, so the tenant console can filter its own customers. Old rows
        # predate the column and belong to the default tenant.
        _ensure_columns(conn, "chat_history", [("tenant_id", "TEXT")])
        try:
            from shared.tenancy.resolver import resolve_default_tenant
            _fallback_tenant = resolve_default_tenant()
        except Exception:
            _fallback_tenant = os.environ.get("DEFAULT_TENANT_ID", "default")
        conn.execute(
            "UPDATE chat_history SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
            (_fallback_tenant,),
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_chat_history_tenant ON chat_history(tenant_id, created_at)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS webhook_dedup (
                message_id VARCHAR(255) PRIMARY KEY,
                wa_id VARCHAR(20),
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dedup_processed ON webhook_dedup(processed_at)"
        )
        # SECURITY FIX: Store payload hash instead of raw payload to prevent sensitive data leakage
        conn.execute(
            """CREATE TABLE IF NOT EXISTS webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                endpoint TEXT,
                payload_hash TEXT,
                payload_size INTEGER,
                status_code INTEGER,
                notes TEXT,
                response_time_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS faq_dataset (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL,
                page_number INTEGER DEFAULT 1,
                embedding BLOB,
                media_url TEXT,
                media_type TEXT,
                module TEXT NOT NULL DEFAULT 'faq'
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY,
                title TEXT,
                category TEXT,
                content TEXT NOT NULL,
                tags TEXT,
                source TEXT,
                chunks_json TEXT,
                embedding_blob BLOB
            )"""
        )
        _ensure_columns(conn, "knowledge_base", [
            ("chunks_json", "TEXT"),
            ("embedding_blob", "BLOB"),
            ("created_at", "TIMESTAMP"),
            ("media_url", "TEXT"),
            ("media_type", "TEXT"),
        ])
        conn.execute(
            """CREATE TABLE IF NOT EXISTS incoming_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE,
                number TEXT NOT NULL,
                message TEXT,
                status TEXT DEFAULT 'received',
                raw_payload TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cached_embeddings (
                chunk_index INTEGER PRIMARY KEY,
                source_name TEXT,
                source_id INTEGER,
                chunk_text TEXT,
                metadata_json TEXT,
                embedding_blob BLOB
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_sessions (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                wa_id TEXT NOT NULL,
                last_inbound_at TIMESTAMP,
                last_outbound_at TIMESTAMP,
                session_open INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                category TEXT DEFAULT 'general',
                description TEXT,
                short_description TEXT,
                price TEXT,
                mrp TEXT,
                unit TEXT DEFAULT 'piece',
                moq TEXT,
                media_url TEXT,
                media_type TEXT DEFAULT 'image',
                ingredients TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        _ensure_columns(conn, "products", [
            ("ingredients", "TEXT DEFAULT '[]'"),
            ("variants_json", "TEXT"),
            ("updated_at", "TIMESTAMP"),
            ("bulk_discount_tiers", "TEXT"),
            ("embedding", "BLOB"),
        ])
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_states (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                wa_id TEXT NOT NULL,
                state TEXT DEFAULT 'MAIN_MENU',
                context_json TEXT DEFAULT '{}',
                lang TEXT DEFAULT 'en',
                version INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        _ensure_columns(conn, "user_states", [("version", "INTEGER DEFAULT 0")])
        # Dedup ledger for channel pollers (send2.digital incoming-report). The
        # poller re-fetches the last hour every cycle and on every restart, so
        # only an on-disk record can keep the bot from re-answering old rows.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS processed_inbound_messages (
                message_id TEXT PRIMARY KEY,
                wa_id TEXT NOT NULL,
                channel TEXT DEFAULT 'send2',
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        # Outbound send-reason ledger — answers "why did this number get a WhatsApp?"
        conn.execute(
            """CREATE TABLE IF NOT EXISTS whatsapp_send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wa_id TEXT NOT NULL,
                tenant_id TEXT,
                reason TEXT NOT NULL,
                trigger_message_id TEXT,
                snippet TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_send_log_wa "
            "ON whatsapp_send_log(wa_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_send_log_tenant_wa "
            "ON whatsapp_send_log(tenant_id, wa_id, created_at)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE NOT NULL,
                wa_id TEXT,
                complaint_type TEXT DEFAULT 'Other',
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                wa_id TEXT,
                items TEXT DEFAULT '[]',
                total_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'placed',
                payment_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT,
                role TEXT DEFAULT 'sub_admin',
                permissions TEXT DEFAULT '{}',
                recovery_key TEXT,
                tenant_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        try:
            # The platform has exactly one super admin.
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS ux_admins_one_super_admin
                   ON admins(role) WHERE role = 'super_admin'"""
            )
            # Each company (tenant) may have at most one admin-role account.
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS ux_admins_one_admin_per_tenant
                   ON admins(tenant_id) WHERE role = 'admin' AND tenant_id IS NOT NULL"""
            )
        except Exception:
            # Pre-existing data may violate the constraint; the API layer
            # still enforces it for every new write.
            logger.debug("ADMIN_UNIQUE_INDEX_SKIPPED | pre-existing data may violate the constraint", exc_info=True)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS admin_otps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS admin_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ext TEXT,
                module TEXT DEFAULT 'kb',
                size INTEGER DEFAULT 0,
                doc_id INTEGER,
                file_path TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        try:
            conn.execute("ALTER TABLE admin_files ADD COLUMN url TEXT")
        except Exception:
            # deliberate: column already exists on migrated DBs.
            logger.debug("SCHEMA_COLUMN_ADD_SKIPPED | table=admin_files column=url", exc_info=True)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS admin_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                sender TEXT,
                username TEXT,
                message TEXT NOT NULL,
                text TEXT,
                attachment_json TEXT,
                deleted_for TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_admin_chat_created_at ON admin_chat_messages(created_at)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_admin_chat_chat_id ON admin_chat_messages(chat_id)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS deleted_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, username)
            )"""
        )
        try:
            conn.execute("ALTER TABLE deleted_chat_messages ADD COLUMN message TEXT")
        except Exception:
            # deliberate: column already exists on migrated DBs.
            logger.debug("SCHEMA_COLUMN_ADD_SKIPPED | table=deleted_chat_messages column=message", exc_info=True)
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_deleted_msg_id ON deleted_chat_messages(message_id)"""
        )
        # Inbox: agent status tracking
        conn.execute(
            """CREATE TABLE IF NOT EXISTS agent_status (
                agent_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'offline',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        # Migration: older DBs created agent_status with "username" column
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_status)").fetchall()]
            if "agent_id" not in cols and "username" in cols:
                conn.execute("ALTER TABLE agent_status RENAME COLUMN username TO agent_id")
        except Exception:
            # deliberate: rename only applies to the legacy layout checked
            # just above; anything else keeps the current schema.
            logger.debug("SCHEMA_COLUMN_RENAME_SKIPPED | table=agent_status", exc_info=True)
        # Inbox: conversation-to-agent assignments
        conn.execute(
            """CREATE TABLE IF NOT EXISTS conversation_assignments (
                tenant_id TEXT NOT NULL DEFAULT 'default',
                wa_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS whatsapp_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                category TEXT DEFAULT 'UTILITY',
                subcategory TEXT DEFAULT 'custom',
                header_type TEXT DEFAULT 'noheader',
                header_text TEXT,
                body_template TEXT NOT NULL,
                footer TEXT DEFAULT '',
                buttons_json TEXT DEFAULT '[]',
                params_json TEXT DEFAULT '[]',
                language TEXT DEFAULT 'en',
                template_for TEXT DEFAULT 'sales',
                status TEXT DEFAULT 'registered',
                send2_response TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                button_id TEXT UNIQUE NOT NULL,
                wa_id TEXT,
                session_id TEXT,
                button_type TEXT NOT NULL,
                label TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                state TEXT DEFAULT 'MAIN_MENU',
                lang TEXT DEFAULT 'en',
                expires_at TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                triggered_at TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS button_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                button_id TEXT NOT NULL,
                wa_id TEXT NOT NULL,
                session_id TEXT,
                button_type TEXT,
                label TEXT,
                payload_json TEXT DEFAULT '{}',
                source TEXT DEFAULT 'whatsapp',
                metadata_json TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        # ── Distributors (B2B contacts / CRM) ────────────────────────────────
        conn.execute(
            """CREATE TABLE IF NOT EXISTS distributors (
                wa_id       TEXT PRIMARY KEY,
                tenant_id   TEXT,
                name        TEXT NOT NULL DEFAULT '',
                phone       TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                region      TEXT DEFAULT '',
                tier        TEXT DEFAULT 'Bronze',
                product_interests TEXT DEFAULT '[]',
                sales_volume      REAL DEFAULT 0,
                last_order_value  REAL DEFAULT 0,
                outstanding_payments REAL DEFAULT 0,
                notes       TEXT DEFAULT '',
                city        TEXT DEFAULT '',
                address     TEXT DEFAULT '',
                service_area TEXT DEFAULT '',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        # ── Campaign opt-outs — consent required by WhatsApp policy ──────────────
        conn.execute(
            """CREATE TABLE IF NOT EXISTS campaign_opt_outs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT NOT NULL,
                wa_id       TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_campaign_opt_outs "
            "ON campaign_opt_outs(tenant_id, wa_id)"
        )

        # ── Segments — named, reusable campaign audiences ────────────────────────
        conn.execute(
            """CREATE TABLE IF NOT EXISTS segments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT NOT NULL,
                name        TEXT NOT NULL,
                audience_type TEXT NOT NULL DEFAULT 'distributors',
                criteria_json TEXT DEFAULT '{}',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_segments_name "
            "ON segments(tenant_id, name)"
        )

        # ── Campaigns ──────────────────────────────────────────────────────────
        conn.execute(
            """CREATE TABLE IF NOT EXISTS campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       TEXT,
                name            TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'draft',
                campaign_type   TEXT NOT NULL DEFAULT 'promotional',
                audience_type   TEXT NOT NULL DEFAULT 'all',
                whatsapp_template TEXT DEFAULT '',
                segment_json    TEXT DEFAULT '{}',
                target_count    INTEGER DEFAULT 0,
                template_type   TEXT NOT NULL DEFAULT 'plain_text',
                message_template TEXT NOT NULL DEFAULT '',
                template_variables_json TEXT DEFAULT '{}',
                buttons_json    TEXT DEFAULT '[]',
                list_items_json TEXT DEFAULT '[]',
                media_filename  TEXT,
                schedule_mode   TEXT NOT NULL DEFAULT 'now',
                scheduled_at    TEXT,
                timezone        TEXT DEFAULT 'Asia/Kolkata',
                sent            INTEGER DEFAULT 0,
                delivered       INTEGER DEFAULT 0,
                read_count      INTEGER DEFAULT 0,
                replied         INTEGER DEFAULT 0,
                failed          INTEGER DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )


        conn.execute(
            """CREATE TABLE IF NOT EXISTS campaign_replies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                wa_id       TEXT NOT NULL,
                name        TEXT DEFAULT '',
                reply_text  TEXT NOT NULL DEFAULT '',
                replied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            )"""
        )

        # Migration: add columns if missing
        _ensure_columns(conn, "admins", [
            ("permissions", "TEXT DEFAULT '{}'"),
            ("recovery_key", "TEXT"),
            ("salt", "TEXT"),
            ("role", "TEXT DEFAULT 'sub_admin'"),
            ("email", "TEXT"),
            ("updated_at", "TIMESTAMP"),
            ("tenant_id", "TEXT"),
        ])
        _ensure_columns(conn, "admin_chat_messages", [
            ("deleted_for", "TEXT"),
            ("sender", "TEXT"),
            ("text", "TEXT"),
            ("attachment_json", "TEXT"),
        ])
        _ensure_columns(conn, "faq_dataset", [
            ("media_url", "TEXT"),
            ("media_type", "TEXT"),
            ("module", "TEXT NOT NULL DEFAULT 'faq'"),
        ])

        _ensure_columns(conn, "products", [
            ("media_url", "TEXT"),
            ("media_type", "TEXT DEFAULT 'image'"),
        ])

        _ensure_columns(conn, "knowledge_base", [
            ("media_url", "TEXT"),
            ("media_type", "TEXT"),
        ])

        _ensure_columns(conn, "webhook_logs", [
            ("response_time_ms", "INTEGER"),
            ("payload_hash", "TEXT"),
            ("payload_size", "INTEGER"),
            ("wa_id", "TEXT"),
            ("tenant_id", "TEXT"),
            ("message_id", "TEXT"),
        ])
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_webhook_logs_wa "
            "ON webhook_logs(wa_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_webhook_logs_tenant_msg "
            "ON webhook_logs(tenant_id, message_id)"
        )

        # B2C support: auto-escalation flag on the user state
        _ensure_columns(conn, "user_states", [
            ("human_handover", "INTEGER DEFAULT 0"),
        ])

        # Timestamp marking when a human session was closed/resolved. NULL while a
        # conversation is (or was never) in human mode; set when control returns
        # to the bot so the live inbox can drop resolved conversations.
        _ensure_columns(conn, "user_states", [
            ("handover_resolved_at", "TIMESTAMP"),
        ])

        conn.execute(
            "UPDATE knowledge_base SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

        # Migrate old admins table: add password_hash column if missing (from old backend schema)
        _ensure_columns(conn, "admins", [
            ("password_hash", "TEXT NOT NULL DEFAULT ''"),
        ])

        # Migrate admin_chat_messages: ensure 'message' column exists for backward compat
        _ensure_columns(conn, "admin_chat_messages", [
            ("message", "TEXT NOT NULL DEFAULT ''"),
        ])
        # -- B2B Distributor migrations (orders / complaints / products) --
        _ensure_columns(conn, "campaigns", [
            ("campaign_type", "TEXT NOT NULL DEFAULT 'promotional'"),
            ("whatsapp_template", "TEXT DEFAULT ''"),
            ("segment_id", "INTEGER"),
            ("variable_fallbacks_json", "TEXT DEFAULT '{}'"),
        ])
        _ensure_columns(conn, "distributors", [
            ("city", "TEXT DEFAULT ''"),
            ("address", "TEXT DEFAULT ''"),
            ("service_area", "TEXT DEFAULT ''"),
        ])
        _ensure_columns(conn, "orders", [
            ("source", "TEXT DEFAULT 'whatsapp'"),
            ("tier", "TEXT DEFAULT 'Bronze'"),
            ("discount_applied", "REAL DEFAULT 0"),
            ("delivery_date", "TEXT"),
            ("customer_name", "TEXT"),
            ("customer_mobile", "TEXT"),
            ("order_type", "TEXT DEFAULT 'order'"),
        ])
        _ensure_columns(conn, "complaints", [
            ("priority", "TEXT DEFAULT 'normal'"),
            ("assigned_to", "TEXT"),
            ("updated_at", "TIMESTAMP"),
            ("resolved_at", "TIMESTAMP"),
            ("subject", "TEXT"),
        ])
        _ensure_columns(conn, "products", [
            ("bulk_discount_tiers", "TEXT DEFAULT '[]'"),
            ("nutritional_facts", "TEXT DEFAULT ''"),
            ("variants_json", "TEXT DEFAULT '[]'"),
            ("updated_at", "TIMESTAMP"),
            ("embedding", "BLOB"),
        ])
        # ── Tenancy for orders + payment confirmations ─────────────────────
        # The tenant consoles (customers / orders) filter by tenant_id, so
        # untagged rows are invisible to tenant admins. New rows are stamped
        # at insert time by the order/payment services; historical rows are
        # backfilled below. payment_confirmations has no CREATE TABLE anywhere
        # in this repo (legacy deployments already have it), so create it
        # if missing to keep fresh databases working.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS payment_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wa_id TEXT NOT NULL,
                amount REAL DEFAULT 0,
                utr_txn_id TEXT,
                order_number TEXT,
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        _ensure_columns(conn, "orders", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "payment_confirmations", [("tenant_id", "TEXT")])
        # Historical rows predate the column. Attribute each wa_id to the
        # tenant of its most recent chat row (a tester's number can move
        # between tenants); wa_ids with no chat history fall back to the
        # configured default tenant.
        conn.execute(
            """UPDATE orders SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = orders.wa_id AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute(
            """UPDATE payment_confirmations SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = payment_confirmations.wa_id AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_orders_tenant ON orders(tenant_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_payment_confirmations_tenant ON payment_confirmations(tenant_id, created_at)")

        # ── Tenancy for uploaded files + the internal admin chat ─────────────
        # `faq_dataset` and `knowledge_base` already carry tenant_id (populated by
        # _init_embedding_tenancy). Uploads also record which brand they belong to
        # so a tenant console only lists its own files; the internal admin chat is
        # per-tenant too.
        _ensure_columns(conn, "admins", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "admin_files", [
            ("tenant_id", "TEXT"),
            ("status", "TEXT DEFAULT 'ready'"),
            ("chunks_count", "INTEGER DEFAULT 0"),
            ("error_message", "TEXT"),
            ("bifurcation_stats", "TEXT"),
        ])
        _ensure_columns(conn, "admin_chat_messages", [("tenant_id", "TEXT")])
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_admin_files_tenant ON admin_files(tenant_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_admin_chat_tenant ON admin_chat_messages(tenant_id, created_at)"
        )

        # ── Knowledge gap analytics (unanswered queries) ──────────────────────
        conn.execute(
            """CREATE TABLE IF NOT EXISTS unanswered_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                wa_id TEXT,
                query TEXT NOT NULL,
                reason TEXT DEFAULT 'low_confidence',
                confidence_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved INTEGER DEFAULT 0,
                resolved_at TIMESTAMP,
                resolved_by TEXT,
                resolution_notes TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_unanswered_queries_tenant ON unanswered_queries(tenant_id, resolved, created_at)"
        )

        # ── Scheduled website re-crawling ──────────────────────────────────────
        # One row per tenant: the URL to re-scrape, how often, and the outcome of
        # the last run. The background scheduler in main.py polls for due rows.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tenant_crawl_schedules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       TEXT NOT NULL UNIQUE,
                url             TEXT NOT NULL,
                interval_hours  INTEGER DEFAULT 24,
                max_pages       INTEGER DEFAULT 8,
                auto_publish    INTEGER DEFAULT 1,
                is_enabled      INTEGER DEFAULT 1,
                last_crawled_at TIMESTAMP,
                last_status     TEXT,
                last_error      TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_crawl_schedules_due ON tenant_crawl_schedules(is_enabled, last_crawled_at)"
        )

        # ── Admin-editable WhatsApp menus ────────────────────────────────────
        # Item IDs are load-bearing: FSM workflows dispatch on exact ids.
        # Only title/description/section/icon/sort_order/is_active are editable.
        # A stale legacy `menus` table (payload/action_type shape, unused by any
        # current code) is renamed rather than dropped so no data is lost.
        _legacy = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='menus'"
        ).fetchone()
        if _legacy:
            _cols = [r[1] for r in conn.execute("PRAGMA table_info(menus)").fetchall()]
            if "menu_key" not in _cols:
                conn.execute("ALTER TABLE menus RENAME TO menus_legacy")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS menus (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                menu_key    TEXT NOT NULL,
                item_id     TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '',
                description TEXT DEFAULT '',
                section     TEXT DEFAULT 'General',
                icon        TEXT DEFAULT '',
                sort_order  INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(menu_key, item_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS menu_settings (
                menu_key   TEXT PRIMARY KEY,
                header     TEXT DEFAULT '',
                body       TEXT DEFAULT '',
                footer     TEXT DEFAULT '',
                button_text TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        # ── Admin-editable dynamic config (publish/draft model) ───────────────
        # Generic staging + publishing for products, price_list, schemes, faq,
        # campaigns, and any other admin-editable JSON snapshots. The published
        # row is what the bot reads; the draft row is what admins edit.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS published_config (
                scope      TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                published_by  TEXT DEFAULT '',
                published_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS draft_config (
                scope      TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                updated_by    TEXT DEFAULT '',
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS publish_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scope         TEXT NOT NULL,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                published_by  TEXT DEFAULT '',
                published_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        # ── Unified cart & checkout (Phase 1) ────────────────────────────────
        # One active cart per user. Totals are COMPUTED on read, never stored.
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS carts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                wa_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('utc')),
                updated_at TEXT NOT NULL DEFAULT (datetime('utc')),
                expires_at TEXT NOT NULL,
                UNIQUE (tenant_id, wa_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_carts_expires ON carts(expires_at)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cart_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cart_id INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL CHECK (qty > 0),
                price_at_add REAL NOT NULL,
                added_at TEXT NOT NULL DEFAULT (datetime('utc')),
                UNIQUE (cart_id, product_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cart_items_cart ON cart_items(cart_id)")

        # Back-compat: the old run_b2c_migration.py schema created cart_items
        # without added_at and without a UNIQUE(cart_id, product_id) constraint
        # that cart_service relies on for its upsert. Reconcile in place.
        _cart_cols = {r[1] for r in conn.execute("PRAGMA table_info(cart_items)").fetchall()}
        if "added_at" not in _cart_cols:
            conn.execute("ALTER TABLE cart_items ADD COLUMN added_at TEXT")
            if "created_at" in _cart_cols:
                conn.execute(
                    "UPDATE cart_items SET added_at = created_at WHERE added_at IS NULL"
                )
            conn.execute(
                "UPDATE cart_items SET added_at = datetime('now') WHERE added_at IS NULL"
            )
            logger.info("Added column added_at to cart_items (legacy schema reconcile)")
        try:
            conn.execute(
                """DELETE FROM cart_items
                   WHERE rowid NOT IN (
                       SELECT MIN(rowid) FROM cart_items
                       GROUP BY cart_id, product_id
                   )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_cart_items_cart_product "
                "ON cart_items(cart_id, product_id)"
            )
        except Exception as e:
            logger.error(f"Failed to create unique index on cart_items: {e}")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS checkout_sessions (
                id TEXT PRIMARY KEY,
                wa_id TEXT NOT NULL,
                cart_snapshot_json TEXT NOT NULL,
                order_draft_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','processing','confirmed','cancelled','expired')),
                state TEXT DEFAULT 'name',
                created_at TEXT NOT NULL DEFAULT (datetime('utc')),
                expires_at TEXT NOT NULL,
                confirmed_order_number TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_checkout_wa ON checkout_sessions(wa_id, status)")
        _ensure_columns(conn, "checkout_sessions", [("state", "TEXT DEFAULT 'name'")])
        # -- Tenancy for carts / checkout / invoice requests / raw incoming --
        # Same rationale as the orders tenancy block: stamped at insert time
        # by the owning services, historical rows backfilled per wa_id (raw
        # incoming messages key on `number`). invoice_requests has no CREATE
        # TABLE elsewhere in the repo, so create it here for fresh databases.
        # inbox_messages / inbox_assignments are legacy orphan tables with no
        # readers or writers and are intentionally left untouched.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS invoice_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wa_id TEXT,
                order_number TEXT,
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        _ensure_columns(conn, "carts", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "cart_items", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "checkout_sessions", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "invoice_requests", [("tenant_id", "TEXT")])
        _ensure_columns(conn, "incoming_messages", [("tenant_id", "TEXT")])
        conn.execute(
            """UPDATE carts SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = carts.wa_id AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute(
            """UPDATE cart_items SET tenant_id = COALESCE((
                   SELECT ca.tenant_id FROM carts ca WHERE ca.id = cart_items.cart_id), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute(
            """UPDATE checkout_sessions SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = checkout_sessions.wa_id AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute(
            """UPDATE invoice_requests SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = invoice_requests.wa_id AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        conn.execute(
            """UPDATE incoming_messages SET tenant_id = COALESCE((
                   SELECT c.tenant_id FROM chat_history c
                   WHERE c.wa_id = incoming_messages.number AND c.tenant_id IS NOT NULL
                   ORDER BY c.id DESC LIMIT 1), ?)
               WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
            (_fallback_tenant,),
        )
        # -- Customer directory ----------------------------------------------
        # One row per person who has ever messaged the tenant. The webhook
        # creates it on first contact (even a plain "Hi") and refreshes it on
        # every later message; lead flows fill in the profile fields
        # customers volunteer. Never duplicated: keyed on (tenant, wa_id).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                wa_id TEXT NOT NULL,
                name TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                city TEXT DEFAULT '',
                state TEXT DEFAULT '',
                company TEXT DEFAULT '',
                language TEXT DEFAULT '',
                message_count INTEGER DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT,
                UNIQUE (tenant_id, wa_id)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_customers_tenant_seen "
            "ON customers(tenant_id, last_seen)"
        )

        # ── Orders: idempotency + per-user history index (Phase 2) ──────────
        _ensure_columns(conn, "orders", [("idempotency_key", "TEXT")])
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idempotency_key
               ON orders(idempotency_key) WHERE idempotency_key IS NOT NULL"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_orders_wa_created ON orders(wa_id, created_at)"
        )
        # Products: optional stock tracking (NULL = untracked).
        _ensure_columns(conn, "products", [("stock_quantity", "INTEGER")])

        _init_tenancy_tables(conn)
        _init_api_onboarding_tables(conn)
        _init_tenant_change_tables(conn)
        _init_admin_audit_tables(conn)
        _init_integration_tables(conn)
        _init_offerings_migration(conn)
        _migrate_products_per_tenant_slug(conn)
        _init_record_columns_table(conn)
        _init_conversation_state_columns(conn)
        _migrate_composite_tenant_wa_keys(conn)
        _ensure_callback_tables(conn)
        try:
            conn.execute("PRAGMA user_version = 2")
        except Exception:
            # deliberate: version stamp is informational — schema setup
            # continues regardless.
            logger.debug("PRAGMA_USER_VERSION_SKIPPED", exc_info=True)


# ── Tenancy: tenants + versioned tenant profiles ─────────────────────────

def _has_legacy_unique_on_phone_id(conn) -> bool:
    """True if the table still carries a column-level UNIQUE on waba_phone_id.

    SQLite bakes those in as auto-indexes with origin 'u'. Indexes we create
    ourselves have origin 'c', so this distinguishes the two reliably. The
    primary key shows up as origin 'pk'.
    """
    for row in conn.execute("PRAGMA index_list(tenants)").fetchall():
        if row["origin"] != "u":
            continue
        name = row["name"]
        cols = [c[2] for c in conn.execute(f"PRAGMA index_info('{name}')").fetchall()]
        if "waba_phone_id" in cols:
            return True
    return False


def _rebuild_tenants_for_partial_unique(conn):
    """Drop the old inline UNIQUE on waba_phone_id.

    Databases created before the partial index carry a column-level UNIQUE,
    which SQLite bakes in as an auto-index that cannot be dropped. Rebuilding the
    table is the only way to remove it. Idempotent: no column-level UNIQUE means
    there is nothing to do. A blank phone id is normalised to NULL on the way
    across, and genuinely duplicate phone ids abort rather than silently
    collapsing two tenants onto one number.
    """
    if not _has_legacy_unique_on_phone_id(conn):
        return

    dupes = conn.execute(
        "SELECT waba_phone_id, COUNT(*) c FROM tenants "
        "WHERE waba_phone_id IS NOT NULL AND waba_phone_id <> '' "
        "GROUP BY waba_phone_id HAVING c > 1"
    ).fetchall()
    if dupes:
        logger.warning(
            "not rebuilding tenants: duplicate waba_phone_id values %s - resolve first",
            [d["waba_phone_id"] for d in dupes],
        )
        return

    conn.execute("ALTER TABLE tenants RENAME TO tenants_legacy_unique")
    conn.execute(
        """CREATE TABLE tenants (
            id              TEXT PRIMARY KEY,
            slug            TEXT UNIQUE,
            waba_phone_id   TEXT,
            vertical        TEXT DEFAULT 'generic',
            status          TEXT DEFAULT 'active',
            display_name    TEXT DEFAULT '',
            webhook_secret  TEXT DEFAULT '',
            domain          TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """INSERT INTO tenants
           (id, slug, waba_phone_id, vertical, status, display_name,
            webhook_secret, domain, created_at, updated_at)
           SELECT id, slug, NULLIF(waba_phone_id, ''), vertical, status, display_name,
                  webhook_secret, domain, created_at, updated_at
           FROM tenants_legacy_unique"""
    )
    conn.execute("DROP TABLE tenants_legacy_unique")
    logger.info("tenants: rebuilt to use a partial unique index on waba_phone_id")


def _init_tenancy_tables(conn):
    """Tenant registry and the versioned profile store.

    JSON lives in TEXT columns (decision D2 — SQLite, no jsonb). The working
    copy an admin edits stays in draft_config under scope
    'tenant_profile:<tenant_id>'; publishing appends a numbered row here and
    flips is_current. Old versions are never deleted, so rollback is a
    re-point, not a restore-from-backup.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenants (
            id              TEXT PRIMARY KEY,
            slug            TEXT UNIQUE,
            waba_phone_id   TEXT,
            vertical        TEXT DEFAULT 'generic',
            status          TEXT DEFAULT 'active',
            display_name    TEXT DEFAULT '',
            webhook_secret  TEXT DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    _ensure_columns(conn, "tenants", [
        ("vertical", "TEXT DEFAULT 'generic'"),
        ("display_name", "TEXT DEFAULT ''"),
        ("webhook_secret", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("waba_phone_id", "TEXT"),
        # First website seen for the tenant, backfilled by save_profile_draft
        # (routes/tenants.py). Without this column a fresh DB 500s on draft save.
        ("domain", "TEXT"),
        # Business mobile for the tenant (required on create). Separate from
        # waba_phone_id which may be a Meta phone-number id.
        ("mobile_number", "TEXT"),
    ])
    # Partial unique index, NOT an inline UNIQUE: a tenant is registered before
    # it is bound to a phone number, and an inline UNIQUE over the default ''
    # would let exactly one unbound tenant exist (registering a second client
    # would fail on the empty string). Blank is stored as NULL and excluded.
    # Drop the old inline UNIQUE first, then add the partial index: a rebuild
    # recreates the table and its indexes, so order matters.
    _rebuild_tenants_for_partial_unique(conn)
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_waba_phone_id
           ON tenants(waba_phone_id)
           WHERE waba_phone_id IS NOT NULL AND waba_phone_id <> ''"""
    )
    # Backfill mobile_number from waba_phone_id when it looks like a phone;
    # otherwise leave a placeholder so existing rows stay queryable until set.
    try:
        conn.execute(
            """UPDATE tenants SET mobile_number = waba_phone_id
               WHERE (mobile_number IS NULL OR TRIM(mobile_number) = '')
                 AND waba_phone_id IS NOT NULL AND length(waba_phone_id) BETWEEN 10 AND 16
                 AND waba_phone_id GLOB '[0-9]*'"""
        )
        conn.execute(
            """UPDATE tenants SET mobile_number = 'UNSET-' || id
               WHERE mobile_number IS NULL OR TRIM(mobile_number) = ''"""
        )
    except Exception as exc:
        logger.warning("tenants mobile_number backfill skipped: %s", exc)
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_mobile_number
           ON tenants(mobile_number)
           WHERE mobile_number IS NOT NULL AND mobile_number <> ''
             AND mobile_number NOT LIKE 'UNSET-%'"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_profile_versions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id    TEXT NOT NULL,
            version      INTEGER NOT NULL,
            payload      TEXT NOT NULL DEFAULT '{}',
            published_by TEXT DEFAULT '',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_current   INTEGER DEFAULT 0,
            UNIQUE(tenant_id, version)
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_profile_current "
        "ON tenant_profile_versions(tenant_id) WHERE is_current = 1"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_profile_versions "
        "ON tenant_profile_versions(tenant_id, version DESC)"
    )
    # Tenant-scoped API tokens (Phase 0.5). Only the hash is stored.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id  TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            label      TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_tokens_tenant ON tenant_tokens(tenant_id)"
    )
    _init_lead_tables(conn)
    _init_embedding_tenancy(conn)


def _init_api_onboarding_tables(conn):
    """API access requests and append-only review events; never store credentials here."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_onboarding_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            api_type TEXT NOT NULL CHECK (api_type IN ('payment_api', 'order_api')),
            provider TEXT NOT NULL DEFAULT '',
            environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            requester_id INTEGER,
            requester_username TEXT NOT NULL DEFAULT '',
            reviewer_id INTEGER,
            reviewer_username TEXT,
            decision_note TEXT NOT NULL DEFAULT '',
            decided_at TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_api_onboarding_pending
           ON api_onboarding_requests(tenant_id, api_type) WHERE status IN ('pending', 'approved')"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_onboarding_entitlements (
            tenant_id TEXT NOT NULL,
            api_type TEXT NOT NULL CHECK (api_type IN ('payment_api', 'order_api')),
            request_id INTEGER NOT NULL REFERENCES api_onboarding_requests(id),
            granted_by INTEGER,
            granted_by_username TEXT NOT NULL DEFAULT '',
            granted_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, api_type),
            UNIQUE (request_id)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_api_onboarding_tenant
           ON api_onboarding_requests(tenant_id, created_at DESC)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_onboarding_request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES api_onboarding_requests(id),
            actor_id INTEGER,
            actor_username TEXT NOT NULL DEFAULT '',
            actor_role TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL CHECK (event_type IN ('submitted', 'decision')),
            old_status TEXT,
            new_status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_onboarding_events_request "
        "ON api_onboarding_request_events(request_id, id)"
    )


def _init_tenant_change_tables(conn):
    """Tenant registrations and profile publishes queued for superadmin approval.

    Admins and sub admins can prepare tenant data, but nothing goes live until
    a super admin approves the request. The payload holds exactly what the
    requester submitted so approval applies what was reviewed, not what the
    draft looks like by the time the request is decided.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_type TEXT NOT NULL
                CHECK (request_type IN ('create_tenant', 'publish_profile')),
            tenant_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            requester_id INTEGER,
            requester_username TEXT NOT NULL DEFAULT '',
            reviewer_id INTEGER,
            reviewer_username TEXT,
            decision_note TEXT NOT NULL DEFAULT '',
            decided_at TEXT,
            applied INTEGER NOT NULL DEFAULT 0,
            applied_version INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_change_requests_status ON tenant_change_requests(status, created_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_change_requests_requester ON tenant_change_requests(requester_id, created_at DESC, id DESC)"
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_change_pending
           ON tenant_change_requests(request_type, tenant_id) WHERE status = 'pending'"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_tenant_change_tenant
           ON tenant_change_requests(tenant_id, created_at DESC)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_change_request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES tenant_change_requests(id) ON DELETE CASCADE,
            actor_id INTEGER,
            actor_username TEXT NOT NULL DEFAULT '',
            actor_role TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL CHECK (event_type IN ('submitted', 'decision')),
            old_status TEXT,
            new_status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_change_events_request ON tenant_change_request_events(request_id, id)"
    )


# ── Callback and Meeting Scheduling Tables ─────────────────────────────────
# These tables support the complete callback booking pipeline with Google Meet integration
def _ensure_callback_tables(conn):
    """Ensure callback-related tables exist."""
    # Main callbacks table - stores all callback requests
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callbacks (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            wa_id TEXT NOT NULL,
            data TEXT NOT NULL,  -- JSON-serialized CallbackRequest with all details
            status TEXT NOT NULL DEFAULT 'pending',
            scheduled_start_time TEXT,
            scheduled_end_time TEXT,
            meet_link TEXT,
            calendar_event_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    # Create indexes for efficient querying
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callbacks_tenant ON callbacks(tenant_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callbacks_wa_id ON callbacks(wa_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callbacks_status ON callbacks(status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callbacks_scheduled ON callbacks(scheduled_start_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callbacks_meet_link ON callbacks(meet_link)")
    
    # Callback summaries table - stores meeting summaries and outcomes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_summaries (
            id TEXT PRIMARY KEY,
            callback_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            meeting_notes TEXT,
            outcome TEXT,
            follow_up_required INTEGER DEFAULT 0,
            follow_up_notes TEXT,
            rating INTEGER,
            feedback TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            FOREIGN KEY (callback_id) REFERENCES callbacks(id) ON DELETE CASCADE
        )
    """)
    
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callback_summaries_callback ON callback_summaries(callback_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callback_summaries_tenant ON callback_summaries(tenant_id, created_at)")
    
    # Agent assignment and availability for callback scheduling
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            agent_id TEXT NOT NULL UNIQUE,
            agent_name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            specialization TEXT,
            max_concurrent_callbacks INTEGER DEFAULT 3,
            current_callbacks INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            UNIQUE(tenant_id, agent_id)
        )
    """)
    
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callback_agents_tenant ON callback_agents(tenant_id, agent_id)")
    
    # Agent availability calendar for callback scheduling
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,  -- YYYY-MM-DD format
            start_time TEXT NOT NULL,  -- HH:MM format
            end_time TEXT NOT NULL,  -- HH:MM format
            is_available INTEGER DEFAULT 1,
            is_booked INTEGER DEFAULT 0,
            callback_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            FOREIGN KEY (agent_id) REFERENCES callback_agents(agent_id),
            FOREIGN KEY (callback_id) REFERENCES callbacks(id),
            UNIQUE(tenant_id, agent_id, date, start_time, end_time)
        )
    """)
    
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_availability_lookup ON agent_availability(tenant_id, agent_id, date, is_available)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_availability_slot ON agent_availability(agent_id, date, start_time)")
    
    # Callback types and categories configuration per tenant
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            type_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            default_duration_minutes INTEGER DEFAULT 30,
            max_duration_minutes INTEGER DEFAULT 120,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            UNIQUE(tenant_id, type_name)
        )
    """)
    
    # Repeating callback schedules (for recurring meetings)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring_callbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            callback_template_id TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'weekly',  -- daily, weekly, biweekly, monthly
            interval INTEGER DEFAULT 1,  -- every N days/weeks/months
            start_date TEXT NOT NULL,  -- YYYY-MM-DD
            end_date TEXT,  -- YYYY-MM-DD (null for indefinite)
            time_of_day TEXT NOT NULL,  -- HH:MM
            timezone TEXT DEFAULT 'Asia/Kolkata',
            max_occurrences INTEGER,  -- max number of recurrences
            current_occurrence INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    # Callback notifications and reminders tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            callback_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,  -- reminder, confirmation, cancellation, reschedule
            recipient_type TEXT NOT NULL,  -- customer, agent, admin
            recipient_contact TEXT NOT NULL,  -- wa_id, email, phone
            message_type TEXT DEFAULT 'whatsapp',  -- whatsapp, email, sms
            message_content TEXT,
            status TEXT DEFAULT 'pending',  -- pending, sent, failed, delivered
            error_message TEXT,
            sent_at TIMESTAMP,
            delivered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (callback_id) REFERENCES callbacks(id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callback_notifications_callback ON callback_notifications(callback_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_callback_notifications_status ON callback_notifications(status, created_at)")
    
    # Google Meet integration tokens and configuration
    conn.execute("""
        CREATE TABLE IF NOT EXISTS google_meet_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL UNIQUE,
            service_account_json TEXT,
            calendar_id TEXT DEFAULT 'primary',
            calendar_name TEXT DEFAULT 'Main Calendar',
            default_meeting_duration INTEGER DEFAULT 30,
            enable_recording INTEGER DEFAULT 0,
            enable_live_stream INTEGER DEFAULT 0,
            meeting_visibility TEXT DEFAULT 'private',
            time_zone TEXT DEFAULT 'Asia/Kolkata',
            is_configured INTEGER DEFAULT 0,
            last_test_at TIMESTAMP,
            last_test_success INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    # Callback analytics and metrics
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,  -- YYYY-MM-DD
            callback_type TEXT,
            status TEXT NOT NULL,
            total_count INTEGER DEFAULT 0,
            total_duration_minutes INTEGER DEFAULT 0,
            avg_duration_minutes REAL DEFAULT 0,
            avg_response_time_minutes REAL DEFAULT 0,
            completion_rate REAL DEFAULT 0,
            cancellation_rate REAL DEFAULT 0,
            agent_id TEXT,
            timezone TEXT DEFAULT 'Asia/Kolkata',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            UNIQUE(tenant_id, date, callback_type, status, agent_id)
        )
    """)
    
    # Call history for callback meetings (for recording actual meeting details)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callback_meeting_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            callback_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            meeting_start_time TIMESTAMP,
            meeting_end_time TIMESTAMP,
            actual_duration_seconds INTEGER,
            participants_json TEXT DEFAULT '[]',  -- JSON array of participants
            recording_url TEXT,
            recording_duration INTEGER,
            transcript_text TEXT,
            quality_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (callback_id) REFERENCES callbacks(id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)

    # Ensure leads table columns match expected profile runner columns
    try:
        existing_cols = {c[1] for c in conn.execute("PRAGMA table_info(leads)").fetchall()}
        for col_name, col_type in [
            ("user_name", "TEXT DEFAULT ''"),
            ("collected", "TEXT DEFAULT '{}'"),
            ("source", "TEXT DEFAULT ''"),
            ("flow", "TEXT DEFAULT ''"),
        ]:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}")
    except Exception:
        logger.exception("CALLBACK_TABLES_ENSURE_FAILED")


def _init_integration_tables(conn):
    """Real connected API credentials, payment transactions, and shipping trackings."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            api_type TEXT NOT NULL CHECK (api_type IN ('payment_api', 'order_api')),
            environment TEXT NOT NULL DEFAULT 'sandbox' CHECK (environment IN ('sandbox', 'production')),
            is_active INTEGER NOT NULL DEFAULT 1,
            credentials_json TEXT NOT NULL DEFAULT '{}',
            webhook_secret TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'configured' CHECK (status IN ('configured', 'verified', 'error')),
            last_tested_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, provider)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_integrations_tenant ON tenant_integrations(tenant_id)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            order_id TEXT,
            provider TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            payment_id TEXT,
            session_id TEXT,
            payment_url TEXT,
            status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'pending', 'paid', 'failed', 'refunded')),
            raw_response TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_payments_tenant ON tenant_payments(tenant_id, created_at DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            order_id TEXT,
            provider TEXT NOT NULL,
            awb_number TEXT NOT NULL,
            tracking_url TEXT,
            status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'in_transit', 'out_for_delivery', 'delivered', 'returned', 'failed')),
            origin_pincode TEXT,
            destination_pincode TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            raw_response TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_shipments_tenant ON tenant_shipments(tenant_id, awb_number)"
    )


# ── Phase 3: data-defined flows write leads and handoffs ─────────────────

def _init_lead_tables(conn):
    """Leads and handoffs, both written by the profile-driven flow runner.

    New idempotent tables, both tenant-scoped. `leads` is the CRM hand-off
    surface (Phase 6 adds the inbox); `handoffs` stores the pending context for
    a human agent. Neither is a second state store: conversation progress stays
    in user_states (decision D3).
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            wa_id       TEXT,
            user_name   TEXT DEFAULT '',
            phone       TEXT DEFAULT '',
            email       TEXT DEFAULT '',
            collected   TEXT DEFAULT '{}',
            status      TEXT DEFAULT 'new'
                CHECK (status IN ('new','contacted','qualified','won','lost','closed')),
            assignee    TEXT DEFAULT '',
            source      TEXT DEFAULT '',
            flow        TEXT DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_tenant_status ON leads(tenant_id, status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_phone ON leads(tenant_id, phone)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS handoffs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id     TEXT NOT NULL DEFAULT 'default',
            wa_id         TEXT NOT NULL,
            user_name     TEXT DEFAULT '',
            reason        TEXT DEFAULT '',
            flow          TEXT DEFAULT '',
            context       TEXT DEFAULT '{}',
            collected     TEXT DEFAULT '{}',
            status        TEXT DEFAULT 'pending'
                CHECK (status IN ('pending','claimed','resolved','expired')),
            assignee      TEXT DEFAULT '',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at   TIMESTAMP,
            expires_at    TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_handoffs_tenant_status ON handoffs(tenant_id, status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_handoffs_wa ON handoffs(wa_id, status)"
    )
    # Complaints gain a tenant so the complaint log is per-brand.
    _ensure_columns(conn, "complaints", [("tenant_id", "TEXT")])
    # Campaigns and distributors are per-brand too: each tenant's console sees
    # only its own broadcasts and resellers.
    _ensure_columns(conn, "campaigns", [("tenant_id", "TEXT")])
    _ensure_columns(conn, "distributors", [("tenant_id", "TEXT")])


# ── Phase 0.4: embedding isolation ───────────────────────────────────────

def _default_tenant_id_for_conn(conn) -> str:
    """The tenant that untagged content belongs to.

    Resolved from the tenants table in this same connection rather than from a
    literal, because the pre-tenant deployment registers its shop as
    'troogood' while the historical placeholder is 'default'. Backfilling to the
    literal would tag TrooGood's 426 FAQ rows with a tenant that does not exist,
    and every tenant-scoped search would then return nothing.
    """
    configured = (os.getenv("DEFAULT_TENANT_ID") or "").strip()
    if configured and conn.execute(
        "SELECT 1 FROM tenants WHERE id = ?", (configured,)
    ).fetchone():
        return configured

    rows = conn.execute(
        "SELECT id FROM tenants WHERE status = 'active' ORDER BY id"
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    if rows:
        # Ambiguous multi-tenant DB: never silently claim untagged rows for the
        # alphabetically first tenant (that mixed Leeway FAQ into MMT tests).
        # Prefer the active-test tenant when set; otherwise leave a sentinel so
        # the backfill only repairs truly orphaned ids, not cross-brand content.
        try:
            active = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'active_test_tenant'"
            ).fetchone()
            active_tid = (
                str(active["value"] if hasattr(active, "keys") else active[0] or "").strip()
                if active else ""
            )
            if active_tid and any(
                (r["id"] if hasattr(r, "keys") else r[0]) == active_tid for r in rows
            ):
                logger.warning(
                    "DEFAULT_TENANT_ID is not set and %d tenants are registered; "
                    "using active_test_tenant=%r for untagged content backfill. "
                    "Set DEFAULT_TENANT_ID to pin this permanently.",
                    len(rows), active_tid,
                )
                return active_tid
        except Exception:
            # deliberate: resolver chain — the warning below covers the
            # overall fallback outcome.
            logger.debug("DEFAULT_TENANT_RESOLVE_STEP_SKIPPED | source=active_test_tenant", exc_info=True)
        logger.warning(
            "DEFAULT_TENANT_ID is not set and %d tenants are registered; "
            "leaving untagged content alone (no auto-claim). Set "
            "DEFAULT_TENANT_ID and re-run init_db to assign orphans.",
            len(rows),
        )
        return ""
    return configured or "default"


def _init_embedding_tenancy(conn):
    """Every stored chunk carries tenant_id; every similarity query filters on it.

    Rows written before tenants existed are backfilled to the default tenant so
    the TrooGood index keeps working untouched.
    """
    _ensure_columns(conn, "cached_embeddings", [
        ("tenant_id", "TEXT DEFAULT 'default'"),
    ])
    _ensure_columns(conn, "faq_dataset", [
        ("tenant_id", "TEXT DEFAULT 'default'"),
    ])
    _ensure_columns(conn, "knowledge_base", [
        ("tenant_id", "TEXT DEFAULT 'default'"),
    ])
    # products.tenant_id is (re)ensured by _init_offerings_migration, which runs
    # after this phase. Ensure it here as well, or the backfill below crashes
    # with "no such column" on a database that has never been initialised.
    _ensure_columns(conn, "products", [
        ("tenant_id", "TEXT"),
    ])
    # Invariant: every content row is owned by a tenant that exists. This both
    # fills blanks and repairs rows left pointing at a tenant id that is no
    # longer registered, so setting DEFAULT_TENANT_ID and re-running init_db
    # re-points previously mis-tagged data.
    resolved = _default_tenant_id_for_conn(conn)
    if not resolved:
        logger.info(
            "content tenant backfill skipped: DEFAULT_TENANT_ID unset with "
            "multiple tenants (refusing to auto-claim rows)"
        )
    else:
        for table in ("cached_embeddings", "faq_dataset", "knowledge_base",
                      "admin_files", "admin_chat_messages", "campaigns", "distributors",
                      "chat_history", "complaints"):
            cur = conn.execute(
                f"UPDATE {table} SET tenant_id = ? "
                f"WHERE tenant_id IS NULL OR TRIM(tenant_id) = '' "
                f"   OR tenant_id NOT IN (SELECT id FROM tenants)",
                (resolved,),
            )
            if cur.rowcount:
                logger.info("%s: tagged %s rows as %s", table, cur.rowcount, resolved)
        # products is special: UNIQUE(tenant_id, slug) is enforced (see
        # _migrate_products_per_tenant_slug), and NULL tenant ids are distinct under
        # UNIQUE — so a blanket re-tag could force two same-slug rows into one
        # tenant and crash init_db. Re-tag row by row; on collision, suffix the slug
        # rather than dropping data or failing startup.
        stray = conn.execute(
            "SELECT id, slug FROM products "
            "WHERE tenant_id IS NULL OR TRIM(tenant_id) = '' "
            "   OR tenant_id NOT IN (SELECT id FROM tenants) ORDER BY id"
        ).fetchall()
        moved = 0
        for row in stray:
            try:
                conn.execute(
                    "UPDATE products SET tenant_id = ? WHERE id = ?",
                    (resolved, row["id"]),
                )
                moved += 1
                continue
            except sqlite3.IntegrityError:
                # deliberate: slug collision for this row — the dedup path
                # below renames and retries it.
                logger.debug("EMBEDDING_TENANCY_ROW_SKIPPED | product_id=%s slug=%s", row["id"], row["slug"], exc_info=True)
            base = (row["slug"] or "product").strip() or "product"
            for k in range(1, 100):
                candidate = (f"{base}-retagged-{row['id']}" if k == 1
                             else f"{base}-retagged-{row['id']}-{k}")
                try:
                    conn.execute(
                        "UPDATE products SET tenant_id = ?, slug = ? WHERE id = ?",
                        (resolved, candidate, row["id"]),
                    )
                    moved += 1
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                logger.warning(
                    "products id=%s: cannot re-tag to %s without a slug collision; "
                    "left untagged", row["id"], resolved,
                )
        if moved:
            logger.info("products: tagged %s rows as %s (collision-safe)", moved, resolved)
        logger.info("content tenant backfill target: %s", resolved)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_cached_embeddings_tenant "
        "ON cached_embeddings(tenant_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_faq_dataset_tenant ON faq_dataset(tenant_id)"
    )


# ── Phase 2: offerings are generic, price is optional ────────────────────

def _init_offerings_migration(conn):
    """products becomes the generic `offerings` table.

    Phase 2: price is optional (a consultancy service has no list price) and
    attrs_json carries vertical detail (tech stack / timeline for IT, duration /
    itinerary for travel). Existing rows are untouched — the columns are
    nullable and default to NULL / '{}'.
    """
    _ensure_columns(conn, "products", [
        ("attrs_json", "TEXT DEFAULT '{}'"),
        ("short_label", "TEXT"),
        ("detail_url", "TEXT"),
        ("tenant_id", "TEXT"),
        ("image_url", "TEXT"),
    ])
    try:
        conn.execute("UPDATE products SET image_url = media_url WHERE (image_url IS NULL OR image_url = '') AND media_url IS NOT NULL")
        conn.execute("UPDATE products SET media_url = image_url WHERE (media_url IS NULL OR media_url = '') AND image_url IS NOT NULL")
    except Exception:
        logger.exception("OFFERINGS_MIGRATION_BACKFILL_FAILED")
    # Price is already a nullable TEXT column, but older rows may hold ''.
    conn.execute("UPDATE products SET price = NULL WHERE TRIM(COALESCE(price, '')) = ''")
    conn.execute("UPDATE products SET attrs_json = '{}' WHERE attrs_json IS NULL")
    # Rows written before offerings became tenant-owned carry no tenant_id. They
    # belong to the default tenant, so backfill rather than leave them orphaned:
    # search_products already filters on tenant_id and would skip NULL rows.
    try:
        from shared.tenancy.resolver import resolve_default_tenant
        default_tenant = resolve_default_tenant()
    except Exception:
        default_tenant = os.environ.get("DEFAULT_TENANT_ID", "default")
    conn.execute(
        "UPDATE products SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
        (default_tenant,),
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_tenant ON products(tenant_id, is_active)"
    )


def _has_legacy_global_unique_on_slug(conn) -> bool:
    """True while products carries the original column-level UNIQUE(slug)."""
    for row in conn.execute("PRAGMA index_list(products)").fetchall():
        if row["origin"] != "u":
            continue
        cols = [c[2] for c in conn.execute(f"PRAGMA index_info('{row['name']}')").fetchall()]
        if cols == ["slug"]:
            return True
    return False


def _migrate_products_per_tenant_slug(conn):
    """Rebuild products so slug is UNIQUE per tenant, not across the table.

    The original schema baked `slug TEXT UNIQUE NOT NULL` in globally, but the
    catalog is tenant-owned: two tenants selling "Personal Loan" could not both
    exist and PDF publishing crashed with IntegrityError after the profile was
    already published. SQLite cannot drop a column constraint in place, so the
    table is rebuilt once (idempotent: origin-'u' index gone means nothing to
    do). Columns are read from PRAGMA so admin-added record columns survive.
    """
    if not _has_legacy_global_unique_on_slug(conn):
        return

    dupes = conn.execute(
        "SELECT tenant_id, slug, COUNT(*) c FROM products "
        "GROUP BY tenant_id, slug HAVING c > 1"
    ).fetchall()
    if dupes:
        logger.warning(
            "not rebuilding products: duplicate (tenant_id, slug) rows %s - resolve first",
            [(d["tenant_id"], d["slug"]) for d in dupes],
        )
        return

    cols = conn.execute("PRAGMA table_info(products)").fetchall()
    col_names = [c["name"] for c in cols]
    defs = []
    for c in cols:
        if c["pk"]:
            # Preserve AUTOINCREMENT semantics of the original table.
            defs.append(f"{c['name']} INTEGER PRIMARY KEY AUTOINCREMENT")
            continue
        piece = f"{c['name']} {c['type'] or 'TEXT'}"
        if c["notnull"]:
            # The inline column-level UNIQUE vanishes by construction (it is
            # re-expressed as UNIQUE(tenant_id, slug)); NOT NULL is preserved.
            piece += " NOT NULL"
        if c["dflt_value"] is not None:
            piece += f" DEFAULT {c['dflt_value']}"
        defs.append(piece)

    # Remember explicit indexes to recreate after the rename (they die with the
    # old table); sqlite_master gives their SQL verbatim.
    kept_index_sql = [
        r["sql"] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='products' "
            "AND sql IS NOT NULL"
        ).fetchall()
    ]

    conn.execute(f"CREATE TABLE products_new ({', '.join(defs)}, UNIQUE (tenant_id, slug))")
    collist = ", ".join(col_names)
    conn.execute(f"INSERT INTO products_new ({collist}) SELECT {collist} FROM products")
    conn.execute("DROP TABLE products")
    conn.execute("ALTER TABLE products_new RENAME TO products")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_products_tenant_slug ON products(tenant_id, slug)"
    )
    for sql in kept_index_sql:
        try:
            conn.execute(sql)
        except Exception as exc:
            logger.warning("rebuild products: could not recreate index (%s): %s", sql, exc)
    logger.info("products: rebuilt with UNIQUE(tenant_id, slug) — slugs are per-tenant now")


# ── Admin-editable record columns (shared/tenancy/records.py) ─────────────

def _init_record_columns_table(conn):
    """The registry of columns each tenant has added to its records table.

    One row per (tenant, column). The physical column lives on `products` and is
    created/dropped by shared.tenancy.records; this table is the labelled,
    ordered, typed view of it that the console renders.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tenant_record_columns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            key TEXT NOT NULL,
            label TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'text',
            required INTEGER DEFAULT 0,
            options_json TEXT DEFAULT '[]',
            help TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            is_system INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, key)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_tenant_record_columns
           ON tenant_record_columns(tenant_id, sort_order)"""
    )
    # Repair the is_system flag on older rows. A previous seed marked the shared
    # presentation columns (price, detail_url, media_url) as system, which locked
    # them in the console (the UI hides delete for system columns) even though
    # shared.tenancy.records treats them as tenant-removable. Re-derive the flag
    # from the single source of truth so existing tenants heal on startup.
    try:
        from shared.tenancy.records import SYSTEM_COLUMNS as _rc_system

        system_keys = [c["key"] for c in _rc_system]
        placeholders = ",".join("?" * len(system_keys))
        conn.execute(
            f"UPDATE tenant_record_columns SET is_system = CASE WHEN key IN ({placeholders}) "
            "THEN 1 ELSE 0 END",
            system_keys,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"record-column is_system repair skipped: {exc}")


# ── Phase 0.3: conversation state on user_states (decision D3) ───────────

def _default_tenant_id() -> str:
    try:
        from shared.tenancy.resolver import resolve_default_tenant
        return resolve_default_tenant()
    except Exception:
        return os.environ.get("DEFAULT_TENANT_ID", "default")


def _table_pk_columns(conn, table: str) -> list:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return []
    pk = []
    for r in rows:
        # (cid, name, type, notnull, dflt, pk)
        name = r[1] if not hasattr(r, "keys") else r["name"]
        pk_ord = r[5] if not hasattr(r, "keys") else r["pk"]
        if pk_ord and int(pk_ord) > 0:
            pk.append((int(pk_ord), name))
    pk.sort()
    return [n for _, n in pk]


def _migrate_composite_tenant_wa_keys(conn):
    """Rebuild session/state/assignment/cart identity as (tenant_id, wa_id).

    Idempotent: skips tables that already have a composite primary key.
    """
    default_tenant = _default_tenant_id()

    # chat_history: per-number lookups must be indexed
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_history_tenant_wa "
        "ON chat_history(tenant_id, wa_id, created_at)"
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_customers_phone ON customers(phone)"
        )
    except Exception:
        logger.exception("CUSTOMER_PHONE_INDEX_FAILED")

    # complaints / orders tenant stamp (orders already have tenant_id in newer DBs)
    _ensure_columns(conn, "complaints", [("tenant_id", "TEXT")])
    _ensure_columns(conn, "orders", [("tenant_id", "TEXT")])
    for table in ("complaints", "orders"):
        try:
            conn.execute(
                f"""UPDATE {table} SET tenant_id = COALESCE((
                       SELECT c.tenant_id FROM customers c
                       WHERE c.wa_id = {table}.wa_id AND c.tenant_id IS NOT NULL
                       ORDER BY c.last_seen DESC LIMIT 1), ?)
                   WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''""",
                (default_tenant,),
            )
        except Exception as exc:
            logger.warning("%s tenant_id backfill skipped: %s", table, exc)
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_wa ON {table}(tenant_id, wa_id)"
        )

    # user_sessions
    if _table_pk_columns(conn, "user_sessions") == ["wa_id"]:
        logger.info("Migrating user_sessions to composite PK (tenant_id, wa_id)")
        _ensure_columns(conn, "user_sessions", [("tenant_id", "TEXT")])
        conn.execute(
            "UPDATE user_sessions SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
            (default_tenant,),
        )
        conn.execute("ALTER TABLE user_sessions RENAME TO user_sessions_legacy_wa")
        conn.execute(
            """CREATE TABLE user_sessions (
                tenant_id TEXT NOT NULL,
                wa_id TEXT NOT NULL,
                last_inbound_at TIMESTAMP,
                last_outbound_at TIMESTAMP,
                session_open INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO user_sessions
               (tenant_id, wa_id, last_inbound_at, last_outbound_at, session_open, created_at)
               SELECT COALESCE(NULLIF(TRIM(tenant_id), ''), ?), wa_id,
                      last_inbound_at, last_outbound_at, session_open, created_at
               FROM user_sessions_legacy_wa""",
            (default_tenant,),
        )
        conn.execute("DROP TABLE user_sessions_legacy_wa")

    # user_states
    if _table_pk_columns(conn, "user_states") == ["wa_id"]:
        logger.info("Migrating user_states to composite PK (tenant_id, wa_id)")
        _ensure_columns(conn, "user_states", [("tenant_id", "TEXT")])
        conn.execute(
            "UPDATE user_states SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
            (default_tenant,),
        )
        # Collect column names that exist so we can copy forward.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_states)").fetchall()]
        conn.execute("ALTER TABLE user_states RENAME TO user_states_legacy_wa")
        conn.execute(
            """CREATE TABLE user_states (
                tenant_id TEXT NOT NULL,
                wa_id TEXT NOT NULL,
                state TEXT DEFAULT 'MAIN_MENU',
                context_json TEXT DEFAULT '{}',
                lang TEXT DEFAULT 'en',
                version INTEGER DEFAULT 0,
                human_handover INTEGER DEFAULT 0,
                handover_resolved_at TIMESTAMP,
                active_flow TEXT,
                current_step TEXT,
                collected TEXT DEFAULT '{}',
                status TEXT DEFAULT 'bot',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        copy_cols = [
            c for c in (
                "tenant_id", "wa_id", "state", "context_json", "lang", "version",
                "human_handover", "handover_resolved_at", "active_flow",
                "current_step", "collected", "status", "updated_at",
            )
            if c in cols or c in ("tenant_id", "wa_id")
        ]
        select_bits = []
        for c in copy_cols:
            if c == "tenant_id":
                select_bits.append("COALESCE(NULLIF(TRIM(tenant_id), ''), ?)")
            elif c in cols:
                select_bits.append(c)
            else:
                select_bits.append("NULL")
        conn.execute(
            f"""INSERT OR IGNORE INTO user_states ({', '.join(copy_cols)})
                SELECT {', '.join(select_bits)} FROM user_states_legacy_wa""",
            (default_tenant,),
        )
        conn.execute("DROP TABLE user_states_legacy_wa")

    # conversation_assignments
    if _table_pk_columns(conn, "conversation_assignments") == ["wa_id"]:
        logger.info("Migrating conversation_assignments to composite PK (tenant_id, wa_id)")
        _ensure_columns(conn, "conversation_assignments", [("tenant_id", "TEXT")])
        conn.execute(
            "UPDATE conversation_assignments SET tenant_id = ? "
            "WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
            (default_tenant,),
        )
        conn.execute(
            "ALTER TABLE conversation_assignments RENAME TO conversation_assignments_legacy_wa"
        )
        conn.execute(
            """CREATE TABLE conversation_assignments (
                tenant_id TEXT NOT NULL,
                wa_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, wa_id)
            )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO conversation_assignments
               (tenant_id, wa_id, agent_id, assigned_at)
               SELECT COALESCE(NULLIF(TRIM(tenant_id), ''), ?), wa_id, agent_id, assigned_at
               FROM conversation_assignments_legacy_wa""",
            (default_tenant,),
        )
        conn.execute("DROP TABLE conversation_assignments_legacy_wa")

    # carts: drop wa_id-only UNIQUE, enforce (tenant_id, wa_id)
    try:
        idx_rows = conn.execute("PRAGMA index_list(carts)").fetchall()
        has_wa_unique = False
        for idx in idx_rows:
            name = idx[1] if not hasattr(idx, "keys") else idx["name"]
            unique = idx[2] if not hasattr(idx, "keys") else idx["unique"]
            if not unique:
                continue
            info = conn.execute(f"PRAGMA index_info({name})").fetchall()
            cols = [r[2] if not hasattr(r, "keys") else r["name"] for r in info]
            if cols == ["wa_id"]:
                has_wa_unique = True
                break
        # Also detect inline UNIQUE on wa_id via table rebuild marker
        create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='carts'"
        ).fetchone()
        sql_text = ""
        if create_sql:
            sql_text = create_sql[0] if not hasattr(create_sql, "keys") else (create_sql["sql"] or "")
        if "wa_id TEXT NOT NULL UNIQUE" in (sql_text or "") or has_wa_unique:
            logger.info("Migrating carts to UNIQUE(tenant_id, wa_id)")
            _ensure_columns(conn, "carts", [("tenant_id", "TEXT")])
            conn.execute(
                "UPDATE carts SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
                (default_tenant,),
            )
            conn.execute("ALTER TABLE carts RENAME TO carts_legacy_wa")
            conn.execute(
                """CREATE TABLE carts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    wa_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('utc')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('utc')),
                    expires_at TEXT NOT NULL,
                    UNIQUE (tenant_id, wa_id)
                )"""
            )
            conn.execute(
                """INSERT OR IGNORE INTO carts
                   (id, tenant_id, wa_id, created_at, updated_at, expires_at)
                   SELECT id, COALESCE(NULLIF(TRIM(tenant_id), ''), ?), wa_id,
                          created_at, updated_at, expires_at
                   FROM carts_legacy_wa""",
                (default_tenant,),
            )
            # Remap cart_items.cart_id stays same (same id values preserved)
            conn.execute("DROP TABLE carts_legacy_wa")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_carts_expires ON carts(expires_at)")
    except Exception as exc:
        logger.warning("carts composite unique migration skipped: %s", exc)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_states_tenant ON user_states(tenant_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_states_wa ON user_states(wa_id)"
    )


def _init_conversation_state_columns(conn):
    """Extend user_states instead of creating a parallel state table (D3).

    One state owner, existing optimistic locking on `version` reused. Flow
    progress lives in active_flow/current_step/collected; handoff expiry stays
    owned by services/handoff_timeout.py — the flow runner only sets/resets
    `status`.
    """
    _ensure_columns(conn, "user_states", [
        ("tenant_id", "TEXT"),
        ("active_flow", "TEXT"),
        ("current_step", "TEXT"),
        ("collected", "TEXT DEFAULT '{}'"),
        ("status", "TEXT DEFAULT 'bot'"),
    ])
    # Conversations that predate the tenant_id column belong to the default
    # tenant. The tenant console scopes chat history by this column, so leaving
    # them NULL would make old customers invisible to the console that owns them.
    default_tenant = _default_tenant_id()
    conn.execute(
        "UPDATE user_states SET tenant_id = ? WHERE tenant_id IS NULL OR TRIM(tenant_id) = ''",
        (default_tenant,),
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_states_tenant ON user_states(tenant_id)"
    )


# ── Generic publish/draft config helpers ──────────────────────────────────


# ── Webhook logging ──────────────────────────────────────────────────────


# ── Chat history ─────────────────────────────────────────────────────────


# ── FAQ dataset helpers ─────────────────────────────────────────────────


# ── Session management (24-hour WhatsApp session window) ─────────────────


# ── Product helpers ──────────────────────────────────────────────────────
#
# Offerings are tenant-owned. Every read and write below resolves to exactly one
# tenant; None means the registered default tenant (single-shop deployments),
# never "all tenants".


# ── WhatsApp Template helpers ────────────────────────────────────────────


# ── Session Button helpers ───────────────────────────────────────────────


# ── Button Click helpers ─────────────────────────────────────────────────


# ── Search helpers ───────────────────────────────────────────────────────


# ── Admin account helpers ────────────────────────────────────────────────


# ── Admin uploaded file registry ─────────────────────────────────────────


# ── Admin-to-admin chat helpers ──────────────────────────────────────────


# ── Knowledge Gap Analytics ───────────────────────────────────────────────────


# ── Scheduled website re-crawling ─────────────────────────────────────────
