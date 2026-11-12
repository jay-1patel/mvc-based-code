"""Auth and tenant-isolation checks for the QA audit fixes.

These call the routers through TestClient so FastAPI actually runs Depends().
Calling the view functions directly would skip that and hide the old
``Depends(require_tenant_access)`` bug.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for _path in (BACKEND_DIR, PROJECT_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import core as db_core
import database
from routes import auth
from routes.admin import router as admin_router
from routes.campaigns import router as campaigns_router
from routes.chat import router as chat_router
from routes.google_connect import router as google_router
from routes.root import router as root_router
from routes.status import router as status_router
from routes.tenants import router as tenants_router
from shared.tenancy.schemas import require_known_vertical


SECRET = "qa-audit-test-secret-key"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router)
    app.include_router(admin_router)
    app.include_router(tenants_router)
    app.include_router(status_router)
    app.include_router(google_router)
    app.include_router(campaigns_router)
    app.include_router(root_router)
    return app


class QaAuditFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "qa.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.core_patch = patch.object(db_core, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.core_patch.start()
        self.secret_patch = patch.object(auth, "ADMIN_SECRET_KEY", SECRET)
        self.secret_patch.start()
        database.init_db()
        self._tenant("acme", "919800000001")
        self._tenant("beta", "919800000002")
        self._admin("admin-acme", "admin", "acme")
        self._admin("admin-beta", "admin", "beta")
        self._admin("super", "super_admin", None)
        with database.get_db_context() as conn:
            conn.execute(
                "INSERT INTO chat_history (wa_id, sender_name, message, response, tenant_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("919111", "Tester", "SECRET_ACME", "reply-acme", "acme"),
            )
            conn.execute(
                "INSERT INTO chat_history (wa_id, sender_name, message, response, tenant_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("919111", "Tester", "SECRET_BETA", "reply-beta", "beta"),
            )
        self.client = TestClient(_app())

    def tearDown(self):
        self.secret_patch.stop()
        self.core_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def _tenant(self, tid, mobile):
        from shared.tenancy import store
        store.ensure_tenant(
            tid,
            slug=tid,
            vertical="ecommerce",
            mobile_number=mobile,
            display_name=tid,
            status="active",
        )

    def _admin(self, username, role, tenant_id):
        database.create_admin(
            username=username,
            password_hash="not-used",
            role=role,
            permissions={
                "manage_operations": True,
                "chat_history": True,
                "view_analytics": True,
                "view_products": True,
            },
            tenant_id=tenant_id,
        )

    def _token(self, username):
        return auth._create_token(username)

    def _auth(self, username):
        return {"Authorization": f"Bearer {self._token(username)}"}

    def test_legacy_chat_history_is_tenant_scoped_and_requires_auth(self):
        open_call = self.client.get("/api/chat/history/919111")
        self.assertEqual(open_call.status_code, 401)

        beta = self.client.get("/api/chat/history/919111", headers=self._auth("admin-beta"))
        self.assertEqual(beta.status_code, 200)
        texts = [m["text"] for m in beta.json()["messages"]]
        self.assertIn("SECRET_BETA", texts)
        self.assertNotIn("SECRET_ACME", texts)

        acme = self.client.get("/api/chat/history/919111", headers=self._auth("admin-acme"))
        texts = [m["text"] for m in acme.json()["messages"]]
        self.assertIn("SECRET_ACME", texts)
        self.assertNotIn("SECRET_BETA", texts)

        admin_global = self.client.get("/api/admin/chat-history", headers=self._auth("admin-beta"))
        blob = str(admin_global.json())
        self.assertIn("SECRET_BETA", blob)
        self.assertNotIn("SECRET_ACME", blob)

        legacy = self.client.get("/chat-history", headers=self._auth("admin-acme"))
        self.assertEqual(legacy.status_code, 200)
        self.assertNotIn("SECRET_BETA", str(legacy.json()))
        self.assertEqual(self.client.get("/chat-history").status_code, 401)

    def test_tenant_directory_and_active_test_switch(self):
        listed = self.client.get("/api/admin/tenants", headers=self._auth("admin-acme"))
        self.assertEqual(listed.status_code, 200)
        ids = [row["id"] for row in listed.json()["tenants"]]
        self.assertEqual(ids, ["acme"])
        self.assertIsNone(listed.json()["active_test_tenant"])

        denied = self.client.post(
            "/api/admin/tenants/active-test-tenant",
            headers=self._auth("admin-acme"),
            json={"tenant_id": "beta"},
        )
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.post(
            "/api/admin/tenants/active-test-tenant",
            headers=self._auth("super"),
            json={"tenant_id": "beta"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["active_test_tenant"], "beta")

    def test_public_leaks_are_closed(self):
        self.assertEqual(self.client.get("/api/admin/analytics/top-questions").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/logs").status_code, 401)
        self.assertEqual(self.client.get("/webhook/logs").status_code, 401)
        self.assertEqual(self.client.delete("/webhook/logs").status_code, 401)
        self.assertEqual(self.client.get("/webhook/health").status_code, 401)
        self.assertEqual(
            self.client.post("/api/tenants/acme/google-connect/disconnect").status_code,
            401,
        )
        campaigns = self.client.get("/api/admin/tenants/acme/campaigns")
        self.assertNotEqual(campaigns.status_code, 200)
        self.assertIn(campaigns.status_code, (401, 403))
        self.assertEqual(self.client.post("/faq/admin/rebuild").status_code, 404)
        # FAQ router is not mounted on this small app. The dependency is still
        # checked on the real app; the rebuild function itself is not awaited.
        from faq.router import rebuild
        import inspect
        self.assertIn("current_admin", inspect.signature(rebuild).parameters)

    def test_analytics_do_not_cross_tenants(self):
        beta = self.client.get("/api/admin/analytics/top-questions", headers=self._auth("admin-beta"))
        self.assertEqual(beta.status_code, 200)
        queries = [q["query"] for q in beta.json()["questions"]]
        self.assertIn("SECRET_BETA", queries)
        self.assertNotIn("SECRET_ACME", queries)

    def test_unknown_vertical_is_rejected(self):
        with self.assertRaises(ValueError):
            require_known_vertical("not-a-vertical")
        self.assertEqual(require_known_vertical("ecommerce"), "ecommerce")
        created = self.client.post(
            "/api/admin/tenants",
            headers=self._auth("super"),
            json={
                "tenant_id": "bad-vert",
                "vertical": "not-a-vertical",
                "mobile_number": "919800000077",
                "auto_scrape": False,
            },
        )
        self.assertEqual(created.status_code, 422)

    def test_forward_and_health_require_auth(self):
        self.assertEqual(self.client.post("/forward", json={"message": "hi"}).status_code, 401)
        health = self.client.get("/webhook/health", headers=self._auth("super"))
        self.assertEqual(health.status_code, 200)
        self.assertNotIn("password", health.text.lower())
        self.assertNotIn("user_name", health.text)


if __name__ == "__main__":
    unittest.main()
