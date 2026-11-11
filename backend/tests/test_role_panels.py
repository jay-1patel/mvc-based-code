"""Role-panels implementation tests (decided design, 2026-11-10).

Covers:
  * super admin is VIEW-ONLY inside tenants — tenant-data writes are
    refused with 403 at the API (upload, publish, campaign create, token
    mint), while system powers keep working (create tenant, approval
    decisions, reads);
  * a tenant admin's own writes still work;
  * audit PDF export returns a real %PDF file;
  * a tenant admin's audit history + errors are scoped to their own
    tenant (another tenant's rows are never returned; asking for another
    tenant is 403);
  * roles are fixed at creation — the PATCH admin endpoint refuses a
    role change (403) while permission-switch edits still work.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import database
from routes import audit as audit_routes
from routes import auth as auth_routes
from routes import campaigns as campaigns_routes
from routes import tenant_approvals as approvals_routes
from routes import tenant_files as tenant_files_routes
from routes import tenants as tenants_routes
from services import error_reporter

PASSWORD = "RootPass123!"


class RolePanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "roles.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.resolver_patch.start()
        # The gate environment has no ADMIN_SECRET_KEY configured (the known
        # baseline gap behind many pre-existing failures); these tests patch
        # one in so the auth flows can actually sign/verify tokens.
        self.key_patch = patch.object(auth_routes, "ADMIN_SECRET_KEY", "test-admin-signing-key")
        self.key_patch.start()
        database.init_db()

        self.app = FastAPI()
        if auth_routes.RATE_LIMIT_ENABLED:
            self.app.state.limiter = auth_routes.limiter
        for module in (auth_routes, tenants_routes, approvals_routes,
                       campaigns_routes, tenant_files_routes, audit_routes):
            self.app.include_router(module.router)
        self.client = TestClient(self.app, raise_server_exceptions=False)

        first = self.client.post(
            "/api/auth/first-admin", json={"username": "root", "password": PASSWORD}
        )
        assert first.status_code == 200, first.text
        self.root = self._login("root")

        created = self.client.post(
            "/api/admin/tenants",
            json={
                "tenant_id": "acme",
                "vertical": "generic",
                "display_name": "Acme Ltd",
                "mobile_number": "+91 98765 43210",
            },
            headers=self.root,
        )
        assert created.status_code == 200, created.text
        self.acme_admin = self._create_admin("acme-admin", tenant_id="acme")

    def tearDown(self):
        self.key_patch.stop()
        self.db_patch.stop()
        self.resolver_patch.stop()
        self.temp.cleanup()

    # ── helpers ────────────────────────────────────────────────────────

    def _login(self, username):
        res = self.client.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert res.status_code == 200, res.text
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def _create_admin(self, username, role="admin", tenant_id=None):
        res = self.client.post(
            "/api/auth/create",
            json={
                "username": username,
                "password": PASSWORD,
                "role": role,
                "permissions": None,
                "email": None,
                "tenant_id": tenant_id,
            },
            headers=self.root,
        )
        assert res.status_code == 200, res.text
        return self._login(username)

    # ── the guard, as pure path classification ─────────────────────────

    def test_guard_path_classification(self):
        guard = auth_routes.is_tenant_data_write
        self.assertTrue(guard("POST", "/api/admin/tenants/acme/files/upload"))
        self.assertTrue(guard("POST", "/api/admin/tenants/acme/publish"))
        self.assertTrue(guard("POST", "/api/admin/tenants/acme/tokens"))
        self.assertTrue(guard("DELETE", "/api/admin/tenants/acme/files/faq.pdf"))
        self.assertTrue(guard("PUT", "/api/admin/tenants/acme/profile"))
        self.assertTrue(guard("POST", "/api/admin/files/upload"))
        # Reads are never writes.
        self.assertFalse(guard("GET", "/api/admin/tenants/acme/files"))
        # System powers a super admin keeps.
        self.assertFalse(guard("POST", "/api/admin/tenants"))
        self.assertFalse(guard("DELETE", "/api/admin/tenants/acme"))
        self.assertFalse(guard("POST", "/api/admin/tenant-change-requests/7/decision"))
        self.assertFalse(guard("POST", "/api/admin/integration-requests/7/decision"))
        self.assertFalse(guard("POST", "/api/auth/create"))
        self.assertFalse(guard("PATCH", "/api/auth/admins/helper"))
        # Capture-only WhatsApp Tester — not a tenant publish/approval write.
        self.assertFalse(guard("POST", "/api/test-simulator"))
        self.assertFalse(guard("POST", "/api/test-simulator/reset"))

    # ── super admin view-only, over HTTP ───────────────────────────────

    def test_super_admin_upload_is_refused(self):
        res = self.client.post(
            "/api/admin/tenants/acme/files/upload",
            files={"file": ("faq.txt", b"question answer", "text/plain")},
            headers=self.root,
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("view-only", res.json()["detail"])

    def test_super_admin_publish_and_token_mint_are_refused(self):
        publish = self.client.post("/api/admin/tenants/acme/publish", headers=self.root)
        publish = self.client.post("/api/admin/tenants/acme/publish", headers=self.root)
        self.assertEqual(publish.status_code, 403, publish.text)
        mint = self.client.post(
            "/api/admin/tenants/acme/tokens", json={"label": "ci"}, headers=self.root
        )
        self.assertEqual(mint.status_code, 403, mint.text)

    def test_super_admin_campaign_create_is_refused(self):
        res = self.client.post(
            "/api/admin/tenants/acme/campaigns",
            json={"name": "Diwali", "message_template": "hi"},
            headers=self.root,
        )
        self.assertEqual(res.status_code, 403, res.text)

    def test_super_admin_reads_and_system_powers_still_work(self):
        detail = self.client.get("/api/admin/tenants/acme/detail", headers=self.root)
        self.assertEqual(detail.status_code, 200, detail.text)
        created = self.client.post(
            "/api/admin/tenants",
            json={
                "tenant_id": "beta",
                "vertical": "generic",
                "display_name": "Beta Ltd",
                "mobile_number": "+91 98765 43211",
            },
            headers=self.root,
        )
        self.assertEqual(created.status_code, 200, created.text)

    def test_super_admin_registration_with_website_still_works(self):
        # Regression (2026-11-11): the register wizard's super flow used to
        # save the basics through PUT /{tenant}/profile after creating,
        # which the view-only guard correctly refuses — registration itself
        # failed. The basics (incl. website) now travel with the create
        # call; the profile endpoint must stay refused for super admins.
        created = self.client.post(
            "/api/admin/tenants",
            json={
                "tenant_id": "leeway",
                "vertical": "it_services",
                "display_name": "Leeway",
                "website": "leewaysoftech.com",
                "auto_scrape": False,
            },
            headers=self.root,
        )
        self.assertEqual(created.status_code, 200, created.text)
        draft = self.client.put(
            "/api/admin/tenants/leeway/profile",
            json={"snapshot": {"brand": {"name": "Leeway"}}},
            headers=self.root,
        )
        self.assertEqual(draft.status_code, 403, draft.text)

    def test_super_admin_approval_decision_still_works(self):
        submitted = self.client.post(
            "/api/tenant-change-requests",
            json={
                "request_type": "create_tenant",
                "tenant_id": "gamma",
                "tenant": {
                    "slug": "gamma",
                    "vertical": "generic",
                    "display_name": "Gamma Ltd",
                    "waba_phone_id": "",
                    "mobile_number": "+91 98765 43212",
                    "status": "active",
                },
                "snapshot": {},
            },
            headers=self.acme_admin,
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)
        request_id = submitted.json()["request"]["id"]
        decided = self.client.post(
            f"/api/admin/tenant-change-requests/{request_id}/decision",
            json={"decision": "approved", "note": "ok"},
            headers=self.root,
        )
        self.assertEqual(decided.status_code, 200, decided.text)

    # ── tenant admin still works ───────────────────────────────────────

    def test_tenant_admin_write_still_works(self):
        saved = self.client.put(
            "/api/admin/tenants/acme/profile",
            json={"snapshot": {"brand": {"name": "Acme Ltd"}}},
            headers=self.acme_admin,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["has_draft"])

    # ── audit: PDF export + tenant scoping ─────────────────────────────

    def _record_event(self, action, tenant_id):
        with database.get_db_context() as conn:
            database.record_admin_audit_event(
                conn,
                action=action,
                actor={"id": 1, "username": "root", "role": "super_admin"},
                resource_type="tenant",
                resource_id=tenant_id,
                tenant_id=tenant_id,
            )

    def test_audit_pdf_export_returns_pdf(self):
        self._record_event("tenant_created", "acme")
        res = self.client.get(
            "/api/admin/audit-export", params={"format": "pdf"}, headers=self.root
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["content-type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"), res.content[:20])
        csv_res = self.client.get(
            "/api/admin/audit-export", params={"format": "csv"}, headers=self.root
        )
        self.assertEqual(csv_res.status_code, 200, csv_res.text)

    def test_admin_audit_history_is_scoped_to_own_tenant(self):
        self._record_event("tenant_created", "acme")
        self._record_event("tenant_created", "other")
        mine = self.client.get("/api/admin/audit-history", headers=self.acme_admin)
        self.assertEqual(mine.status_code, 200, mine.text)
        events = mine.json()["events"]
        self.assertTrue(events)
        self.assertTrue(all(e["tenant_id"] == "acme" for e in events))
        other = self.client.get(
            "/api/admin/audit-history",
            params={"tenant_id": "other"},
            headers=self.acme_admin,
        )
        self.assertEqual(other.status_code, 403, other.text)
        everything = self.client.get("/api/admin/audit-history", headers=self.root)
        self.assertEqual(everything.status_code, 200, everything.text)
        tenants_seen = {e["tenant_id"] for e in everything.json()["events"]}
        self.assertIn("other", tenants_seen)

    def test_admin_errors_are_scoped_to_own_tenant(self):
        error_reporter.report_error("api", "GET /x", "boom-acme", tenant_id="acme")
        error_reporter.report_error("api", "GET /y", "boom-other", tenant_id="other")
        from routes.api_errors import get_api_error, list_api_errors

        admin = {"role": "admin", "tenant_id": "acme", "username": "acme-admin"}
        rows = list_api_errors(limit=50, provider=None, tenant_id=None, current_admin=admin)
        self.assertTrue(rows["errors"])
        self.assertTrue(all(r["tenant_id"] == "acme" for r in rows["errors"]))
        with self.assertRaises(HTTPException) as ctx:
            list_api_errors(limit=50, provider=None, tenant_id="other", current_admin=admin)
        self.assertEqual(ctx.exception.status_code, 403)
        other_id = next(
            r["id"] for r in error_reporter.list_recent_errors(tenant_id="other")
        )
        with self.assertRaises(HTTPException) as ctx:
            get_api_error(other_id, current_admin=admin)
        self.assertEqual(ctx.exception.status_code, 403)

    # ── role change does not exist ─────────────────────────────────────

    def test_role_change_is_refused_but_switch_edits_work(self):
        self._create_admin("helper", role="sub_admin", tenant_id="acme")
        change = self.client.patch(
            "/api/auth/admins/helper", json={"role": "admin"}, headers=self.root
        )
        self.assertEqual(change.status_code, 403, change.text)
        self.assertIn("fixed at account creation", change.json()["detail"])
        self.assertEqual(database.get_admin_record("helper")["role"], "sub_admin")

        edit = self.client.patch(
            "/api/auth/admins/helper",
            json={"permissions": {"view_customers": True}},
            headers=self.root,
        )
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(database.get_admin_record("helper")["role"], "sub_admin")


if __name__ == "__main__":
    unittest.main()
