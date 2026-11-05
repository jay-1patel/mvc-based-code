"""
Database migration for B2C cart/checkout feature.

Run once:
    python -c "from backend.migrations.run_b2c_migration import run; run()"

Safe to re-run (idempotent).
"""
import logging
import sys
import os

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
from database import get_db_context

logger = logging.getLogger("b2c_migration")


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _index_exists(conn, index: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index,),
    ).fetchone()
    return row is not None


def run():
    with get_db_context() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # 1. user_states: optimistic-lock version column
        if not _column_exists(conn, "user_states", "version"):
            conn.execute(
                "ALTER TABLE user_states ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added user_states.version")

        # 2. orders: new columns for B2C support
        for col, defn in [
            ("order_type", "VARCHAR(10) DEFAULT 'b2b'"),
            ("payment_method", "VARCHAR(20)"),
            ("customer_name", "VARCHAR(100)"),
            ("customer_mobile", "VARCHAR(20)"),
            ("customer_address", "TEXT"),
            ("customer_pincode", "VARCHAR(10)"),
            ("idempotency_key", "VARCHAR(255)"),
        ]:
            if not _column_exists(conn, "orders", col):
                conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {defn}")
                logger.info(f"Added orders.{col}")

        # 3. Indexes on orders
        if not _index_exists(conn, "idx_orders_order_type"):
            conn.execute("CREATE INDEX idx_orders_order_type ON orders(order_type)")
        if not _index_exists(conn, "idx_orders_wa_id_created"):
            conn.execute(
                "CREATE INDEX idx_orders_wa_id_created ON orders(wa_id, created_at DESC)"
            )

        # 4. UNIQUE partial index on idempotency_key (SQLite allows multiple NULLs)
        if not _index_exists(conn, "uq_orders_idempotency_key"):
            conn.execute(
                "CREATE UNIQUE INDEX uq_orders_idempotency_key "
                "ON orders(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            logger.info("Created unique index on orders.idempotency_key")

        # 5. user_consents table (DPDP)
        if not _table_exists(conn, "user_consents"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_consents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wa_id VARCHAR(20) NOT NULL,
                    consent_type VARCHAR(50) NOT NULL,
                    opted_in BOOLEAN DEFAULT 1,
                    channel VARCHAR(20) DEFAULT 'whatsapp',
                    consent_text TEXT,
                    opted_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    opted_out_at TIMESTAMP,
                    UNIQUE(wa_id, consent_type)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_consents_wa_id ON user_consents(wa_id)"
            )
            logger.info("Created user_consents")

        # 6. webhook_dedup table (authoritative L2 dedup)
        if not _table_exists(conn, "webhook_dedup"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhook_dedup (
                    message_id VARCHAR(255) PRIMARY KEY,
                    wa_id VARCHAR(20),
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dedup_processed ON webhook_dedup(processed_at)"
            )
            logger.info("Created webhook_dedup")

        # 7. whatsapp_outbox table (send retry)
        if not _table_exists(conn, "whatsapp_outbox"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wa_id VARCHAR(20) NOT NULL,
                    message_type VARCHAR(20) NOT NULL,
                    payload TEXT NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    attempts INTEGER DEFAULT 0,
                    next_retry TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_at TIMESTAMP
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_status_retry "
                "ON whatsapp_outbox(status, next_retry)"
            )
            logger.info("Created whatsapp_outbox")

        # 8. serviceable_pincodes table (B2C COD delivery)
        if not _table_exists(conn, "serviceable_pincodes"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS serviceable_pincodes (
                    pincode VARCHAR(10) PRIMARY KEY,
                    region VARCHAR(50),
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            _seed_pincodes(conn)
            logger.info("Created serviceable_pincodes")

        # 9. Canonical DB-backed cart layer (additive, idempotent)
        if not _table_exists(conn, "carts"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS carts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wa_id TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(wa_id, status)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_carts_wa_status ON carts(wa_id, status)"
            )
            logger.info("Created carts")

        if not _table_exists(conn, "cart_items"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cart_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cart_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    price_at_add REAL NOT NULL DEFAULT 0,
                    qty INTEGER NOT NULL DEFAULT 1,
                    media_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cart_items_cart_id ON cart_items(cart_id)"
            )
            logger.info("Created cart_items")

        if not _table_exists(conn, "checkout_sessions"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkout_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wa_id TEXT NOT NULL,
                    cart_id INTEGER,
                    order_draft_json TEXT DEFAULT '{}',
                    state TEXT DEFAULT 'name',
                    idempotency_key TEXT UNIQUE,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE SET NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkout_sessions_wa_id ON checkout_sessions(wa_id)"
            )
            logger.info("Created checkout_sessions")

    logger.info("B2C migration complete")


def _seed_pincodes(conn):
    pincodes = [
        ("380001", "Ahmedabad"), ("380002", "Ahmedabad"), ("380006", "Ahmedabad"),
        ("380007", "Ahmedabad"), ("380008", "Ahmedabad"), ("380009", "Ahmedabad"),
        ("380013", "Ahmedabad"), ("380015", "Ahmedabad"), ("380016", "Ahmedabad"),
        ("380019", "Ahmedabad"), ("380021", "Ahmedabad"), ("380022", "Ahmedabad"),
        ("380023", "Ahmedabad"), ("380024", "Ahmedabad"), ("380026", "Ahmedabad"),
        ("380027", "Ahmedabad"), ("380028", "Ahmedabad"), ("380051", "Ahmedabad"),
        ("380054", "Ahmedabad"), ("380055", "Ahmedabad"), ("380058", "Ahmedabad"),
        ("380059", "Ahmedabad"), ("380060", "Ahmedabad"), ("380061", "Ahmedabad"),
        ("380063", "Ahmedabad"), ("382010", "Gandhinagar"), ("382016", "Gandhinagar"),
        ("382021", "Gandhinagar"), ("382028", "Gandhinagar"), ("382110", "Gandhinagar"),
        ("390001", "Vadodara"), ("390002", "Vadodara"), ("390003", "Vadodara"),
        ("390004", "Vadodara"), ("390005", "Vadodara"), ("390007", "Vadodara"),
        ("390008", "Vadodara"), ("390009", "Vadodara"), ("390010", "Vadodara"),
        ("390011", "Vadodara"), ("390012", "Vadodara"), ("390013", "Vadodara"),
        ("390015", "Vadodara"), ("390019", "Vadodara"), ("390020", "Vadodara"),
        ("395001", "Surat"), ("395002", "Surat"), ("395003", "Surat"),
        ("395004", "Surat"), ("395005", "Surat"), ("395006", "Surat"),
        ("395007", "Surat"), ("395008", "Surat"), ("395009", "Surat"),
        ("395010", "Surat"), ("395011", "Surat"), ("395012", "Surat"),
        ("360001", "Rajkot"), ("360002", "Rajkot"), ("360003", "Rajkot"),
        ("360004", "Rajkot"), ("360005", "Rajkot"), ("360006", "Rajkot"),
        ("360007", "Rajkot"), ("361001", "Jamnagar"), ("362001", "Junagadh"),
        ("363001", "Surendranagar"), ("364001", "Bhavnagar"), ("370001", "Bhuj"),
    ]
    for pincode, region in pincodes:
        conn.execute(
            "INSERT OR IGNORE INTO serviceable_pincodes (pincode, region) VALUES (?, ?)",
            (pincode, region),
        )


if __name__ == "__main__":
    run()
