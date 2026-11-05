"""Superadmin tenant delete must permanently wipe tenant-scoped DB + client pack."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from shared.tenancy import store as tenancy_store


class TenantHardDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "hard_delete.sqlite3")
        self.clients_root = Path(self.temp.name) / "clients"
        self.clients_root.mkdir(parents=True, exist_ok=True)
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.clients_patch = patch("shared.tenancy.paths.CLIENTS_DIR", self.clients_root)
        self.db_patch.start()
        self.clients_patch.start()
        database.init_db()

        self.tid = "purge-me"
        tenancy_store.ensure_tenant(
            self.tid, slug="purge-me", vertical="banking", display_name="Purge Me"
        )
        tenancy_store.save_draft(
            self.tid,
            {"display_name": "Purge Me", "brand": {"name": "Purge Me"}},
            updated_by="test",
        )
        with database.get_db_context() as conn:
            conn.execute(
                "INSERT INTO products (name, slug, tenant_id, is_active) VALUES (?, ?, ?, 1)",
                ("Loan Pack", "loan-pack", self.tid),
            )
            conn.execute(
                "INSERT INTO faq_dataset (source_file, content_type, content, tenant_id) "
                "VALUES (?, 'text', 'hello', ?)",
                ("kit.pdf", self.tid),
            )
            conn.execute(
                "INSERT INTO customers (tenant_id, wa_id, name) VALUES (?, ?, ?)",
                (self.tid, "919999999999", "Test User"),
            )
            conn.execute(
                "INSERT INTO chat_history (wa_id, message, response, tenant_id) "
                "VALUES (?, 'hi', 'hello', ?)",
                ("919999999999", self.tid),
            )
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                ("active_test_tenant", self.tid),
            )
            conn.execute(
                "INSERT INTO admin_audit_events "
                "(created_at, actor_username, actor_role, action, tenant_id, outcome) "
                "VALUES (datetime('now'), 'ops', 'super_admin', 'tenant_created', ?, 'success')",
                (self.tid,),
            )

        pack = self.clients_root / self.tid
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "config.json").write_text('{"tenant_id":"purge-me"}', encoding="utf-8")
        (pack / "knowledge").mkdir(exist_ok=True)

    def tearDown(self):
        self.db_patch.stop()
        self.clients_patch.stop()
        self.temp.cleanup()

    def test_purge_removes_tenant_scoped_rows_and_disk_keeps_audit(self):
        summary = tenancy_store.purge_tenant_data(self.tid, slug="purge-me")

        self.assertEqual(summary["tables"].get("tenants"), 1)
        self.assertGreaterEqual(summary["tables"].get("products", 0), 1)
        self.assertGreaterEqual(summary["tables"].get("faq_dataset", 0), 1)
        self.assertGreaterEqual(summary["tables"].get("customers", 0), 1)
        self.assertGreaterEqual(summary["tables"].get("chat_history", 0), 1)
        self.assertIsNone(tenancy_store.get_tenant(self.tid))
        self.assertFalse((self.clients_root / self.tid).exists())

        with database.get_db_context() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM products WHERE tenant_id = ?",
                    (self.tid,),
                ).fetchone()["c"],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM faq_dataset WHERE tenant_id = ?",
                    (self.tid,),
                ).fetchone()["c"],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM customers WHERE tenant_id = ?",
                    (self.tid,),
                ).fetchone()["c"],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM draft_config WHERE scope = ?",
                    (tenancy_store.profile_scope(self.tid),),
                ).fetchone()["c"],
                0,
            )
            active = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                ("active_test_tenant",),
            ).fetchone()
            self.assertIsNone(active)
            # Prior audit history for this tenant is retained.
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM admin_audit_events WHERE tenant_id = ?",
                    (self.tid,),
                ).fetchone()["c"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
