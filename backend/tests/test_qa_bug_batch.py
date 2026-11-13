"""QA bug batch 2026-11-13 — regression tests.

Fix 1: sub-admin presets locked out of tenant READS (the tenant-access gate
       demanded manage_operations for GETs; no preset grants it).
Fix 3: duplicate offering create crashed 500 (UNIQUE tenant+slug) -> 409.
Fix 4: corrupt upload ended status 'ready', 0 chunks, empty error; and the
       upload size cap is 25 MB with HTTP 413.
Fix 5: heuristic scrape fallback fabricated template FAQs (incl. invented
       business hours) -> the fallback now stages zero FAQs.
Fix 6: B2B/B2C greeting leaked the placeholder brand name "Brand".
Fix 7: super admin could reset a TENANT user's password (D-05) -> 403;
       platform accounts and tenant-admin resets keep working.
Fix 8: super admin tenant-scoped rebuild-index (D-21) -> 403; the global
       rebuild stays a platform power.
Fix 9: scrape fallback invented defaults scrubbed — the heuristic stamped
       09:30-18:30 Mon-Sat hours and a +91 9999999999 phone on every site,
       and the LLM prompt's schema example embedded the same invented
       hours. Unstated values are now absent, and the prompt forbids
       inventing facts.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for _p in (BACKEND_DIR, PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("ADMIN_SECRET_KEY", "qa-bug-batch-secret")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from shared.tenancy import store as tenancy_store

TENANT = "qa-tenant"

# The four sub-admin presets, copied from frontend/src/lib/permissions.ts
# (SUB_ADMIN_PRESETS) — the backend has no preset table; a preset account is
# simply a sub_admin whose permission switches are exactly these.
PRESETS = {
    "support": ["view_inbox", "chat_history", "view_complaints", "manage_complaints", "view_customers", "chat"],
    "content": [
        "view_files", "upload_faq", "upload_kb", "view_products",
        "add_product", "edit_product", "manage_services",
        "manage_projects", "manage_technologies", "manage_careers", "manage_benefits",
    ],
    "operations": ["view_orders", "manage_orders", "view_customers", "view_distributors"],
    "growth": ["view_campaigns", "manage_campaigns", "view_analytics"],
}

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _preset_perms(name: str) -> dict:
    return {switch: True for switch in PRESETS[name]}


class _BatchBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "qa-batch.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.clients_root = Path(self.temp.name) / "clients"
        self.clients_root.mkdir()
        self._patches = [
            patch.object(database, "DB_PATH", self.db_path),
            patch.dict(os.environ, {"UPLOAD_DIR": str(self.upload)}),
            patch("shared.tenancy.paths.CLIENTS_DIR", self.clients_root),
            patch(
                "shared.tenancy.resolver.resolve_default_tenant",
                return_value="default",
            ),
        ]
        for p in self._patches:
            p.start()
        database.init_db()
        tenancy_store.ensure_tenant(
            TENANT, slug=TENANT, vertical="ecommerce", display_name="QA Test Store"
        )
        from routes.auth import _create_token, _hash_password

        self._hash = _hash_password
        self._token = _create_token
        self._mk_admin("qa_super", "super_admin", None, None)
        self._mk_admin("qa_platform_admin", "admin", None, None)
        self._mk_admin("qa_tenant_admin", "admin", TENANT, None)
        self._mk_admin("qa_content", "sub_admin", TENANT, _preset_perms("content"))
        self._mk_admin("qa_growth", "sub_admin", TENANT, _preset_perms("growth"))
        self._mk_admin("qa_ops", "sub_admin", TENANT, _preset_perms("operations"))
        self._mk_admin("qa_tenant_sub", "sub_admin", TENANT, _preset_perms("support"))

    def tearDown(self):
        for p in reversed(self._patches):
            try:
                p.stop()
            except Exception:
                pass
        self.temp.cleanup()

    def _mk_admin(self, username, role, tenant_id, permissions):
        database.create_admin(
            username=username,
            password_hash=self._hash(STRONG_PASSWORD),
            role=role,
            permissions=permissions or {},
            tenant_id=tenant_id,
        )

    def _h(self, username: str) -> dict:
        return {"Authorization": f"Bearer {self._token(username)}"}

    def _client(self, *routers) -> TestClient:
        app = FastAPI()
        for r in routers:
            app.include_router(r)
        return TestClient(app, raise_server_exceptions=False)


class PresetReadTests(_BatchBase):
    """Fix 1 — preset sub-admins can read their tenant's data; writes and
    per-route view switches are unchanged."""

    def setUp(self):
        super().setUp()
        from routes import campaigns, offerings, tenant_files, tenant_operations

        self.client = self._client(
            tenant_files.router,
            offerings.router,
            campaigns.router,
            tenant_operations.router,
        )

    def test_content_preset_reads_files_and_offerings(self):
        r = self.client.get(f"/api/admin/tenants/{TENANT}/files", headers=self._h("qa_content"))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/admin/tenants/{TENANT}/offerings", headers=self._h("qa_content"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_growth_preset_reads_campaign_templates(self):
        # list_templates is guarded by the bare tenant-access gate (no extra
        # view switch), so this is the pure Fix-1 path: pre-fix 403.
        r = self.client.get(
            f"/api/admin/tenants/{TENANT}/campaigns/templates", headers=self._h("qa_growth")
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_operations_preset_reads_customers(self):
        r = self.client.get(f"/api/admin/tenants/{TENANT}/customers", headers=self._h("qa_ops"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_read_still_needs_the_route_view_switch(self):
        # Growth holds no view_files switch: the per-route switch still
        # refuses the read. The fix removed only the manage_operations
        # demand at the gate, not per-screen authorization.
        r = self.client.get(f"/api/admin/tenants/{TENANT}/files", headers=self._h("qa_growth"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_write_still_refused_without_manage_operations(self):
        # Content HAS add_product, but a tenant-data write still requires
        # manage_operations at the gate — unchanged by Fix 1.
        r = self.client.post(
            f"/api/admin/tenants/{TENANT}/offerings",
            headers=self._h("qa_content"),
            json={"name": "Preset Widget", "category": "Widgets"},
        )
        self.assertEqual(r.status_code, 403, r.text)


class DuplicateOfferingTests(_BatchBase):
    """Fix 3 — the concurrent-duplicate create returns 409, not 500."""

    def setUp(self):
        super().setUp()
        from routes import offerings

        self.offerings = offerings
        self.client = self._client(offerings.router)

    def test_first_create_succeeds_duplicate_create_is_409(self):
        h = self._h("qa_tenant_admin")
        r1 = self.client.post(
            f"/api/admin/tenants/{TENANT}/offerings",
            headers=h,
            json={"name": "Race Widget", "category": "Widgets", "price": "499"},
        )
        self.assertEqual(r1.status_code, 200, r1.text)
        # Simulate the QA race: two creates computing the SAME slug before
        # either commits (the UNIQUE failure in the QA errors log). The slug
        # helper can only dedupe what it can see, so force the collision.
        with patch.object(self.offerings, "_unique_slug", return_value="race-widget"):
            r2 = self.client.post(
                f"/api/admin/tenants/{TENANT}/offerings",
                headers=h,
                json={"name": "Race Widget", "category": "Widgets", "price": "499"},
            )
        self.assertEqual(r2.status_code, 409, r2.text)
        self.assertIn("already exists", r2.json().get("detail", ""))


class UploadFailureTests(_BatchBase):
    """Fix 4 — corrupt uploads fail loudly; >25 MB uploads get HTTP 413."""

    def test_corrupt_pdf_ends_failed_with_message(self):
        from routes import admin as admin_routes

        bad = self.upload / "corrupt.pdf"
        bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf at all\x00\x01\x02")
        database.save_admin_file(
            name="corrupt.pdf", ext=".pdf", module="faq", size=bad.stat().st_size,
            file_path=str(bad), status="processing",
        )
        admin_routes._process_document_background(
            str(bad), "corrupt.pdf", ".pdf", "faq", "", "", None
        )
        record = database.get_admin_file("corrupt.pdf")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed", record)
        self.assertTrue((record.get("error_message") or "").strip(), record)

    def test_valid_pdf_ends_ready(self):
        import fitz
        from routes import admin as admin_routes

        good = self.upload / "valid.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "What is the warranty? The warranty lasts two years.")
        doc.save(str(good))
        doc.close()
        database.save_admin_file(
            name="valid.pdf", ext=".pdf", module="faq", size=good.stat().st_size,
            file_path=str(good), status="processing",
        )
        admin_routes._process_document_background(
            str(good), "valid.pdf", ".pdf", "faq", "", "", None
        )
        record = database.get_admin_file("valid.pdf")
        self.assertEqual(record["status"], "ready", record)

    def test_oversize_upload_is_413(self):
        from routes import tenant_files

        client = self._client(tenant_files.router)
        big = b"%PDF-1.4\n" + b"x" * (26 * 1024 * 1024)
        r = client.post(
            f"/api/admin/tenants/{TENANT}/files/upload?module=faq",
            headers=self._h("qa_tenant_admin"),
            files={"file": ("big.pdf", big, "application/pdf")},
        )
        self.assertEqual(r.status_code, 413, r.text[:300])
        self.assertIn("25 MB", r.text)


class TemplateFaqTests(unittest.TestCase):
    """Fix 5 — the heuristic scrape fallback stages NO FAQs."""

    def test_heuristic_extraction_produces_no_faqs(self):
        from services.url_scraper import UrlAutoOnboarder

        crawl_res = {
            "domain": "example.com",
            "root_url": "https://example.com/",
            "pages": [
                {
                    "url": "https://example.com/",
                    "title": "Example Co - Handmade Widgets",
                    "category": "home",
                    "content": "We sell handmade widgets and gadgets for homes.",
                    "headings": ["Handmade Widgets"],
                    "meta_description": "Handmade widgets and gadgets",
                    "emails": [],
                    "phones": [],
                    "images": [],
                    "logo_url": None,
                    "json_ld_offerings": [],
                    "visual_cards": [],
                }
            ],
        }
        result = UrlAutoOnboarder()._heuristic_extraction(crawl_res, "example.com")
        self.assertEqual(result["faqs"], [])
        self.assertNotIn("operating business hours", json.dumps(result["faqs"]))


def _scrape_fixture(*, phones, content="We sell handmade widgets and gadgets for homes."):
    return {
        "domain": "example.com",
        "root_url": "https://example.com/",
        "pages": [
            {
                "url": "https://example.com/",
                "title": "Example Co - Handmade Widgets",
                "category": "home",
                "content": content,
                "headings": ["Handmade Widgets"],
                "meta_description": "Handmade widgets and gadgets",
                "emails": [],
                "phones": phones,
                "images": [],
                "logo_url": None,
                "json_ld_offerings": [],
                "visual_cards": [],
            }
        ],
    }


class ScrubbedDefaultsTests(unittest.TestCase):
    """Fix 9 — the scrape fallback invents no hours and no phone number."""

    def test_no_stated_phone_or_hours_yields_absent_values(self):
        from services.url_scraper import UrlAutoOnboarder

        result = UrlAutoOnboarder()._heuristic_extraction(
            _scrape_fixture(phones=[]), "example.com"
        )
        self.assertEqual(result["brand"]["support_phone"], "")
        self.assertFalse(result["business_hours"], result["business_hours"])
        blob = json.dumps(result)
        self.assertNotIn("09:30", blob)
        self.assertNotIn("18:30", blob)
        self.assertNotIn("9999999999", blob)

    def test_stated_phone_is_kept_exactly_and_hours_stay_absent(self):
        from services.url_scraper import UrlAutoOnboarder

        result = UrlAutoOnboarder()._heuristic_extraction(
            _scrape_fixture(
                phones=["+91 98765 43210"],
                content="Call us for widgets. Open 10 AM to 8 PM, Monday to Saturday.",
            ),
            "example.com",
        )
        self.assertEqual(result["brand"]["support_phone"], "+91 98765 43210")
        # The heuristic never parses prose hours; it must not stamp its
        # old invented 09:30-18:30 defaults over whatever the page says.
        self.assertFalse(result["business_hours"], result["business_hours"])

    def test_llm_prompt_example_carries_no_invented_hours(self):
        from services.url_scraper import UrlAutoOnboarder

        with patch.object(
            UrlAutoOnboarder, "_call_llm", return_value=None
        ) as mock_llm:
            result = UrlAutoOnboarder().extract_bifurcation(
                _scrape_fixture(phones=[])
            )
        self.assertEqual(result["bifurcation_source"], "heuristic")
        system_prompt = mock_llm.call_args[0][0]
        self.assertNotIn('"09:30"', system_prompt)
        self.assertNotIn('"18:30"', system_prompt)
        self.assertIn("NEVER invent facts", system_prompt)


class GreetingBrandTests(_BatchBase):
    """Fix 6 — the greeting never leaks the placeholder name "Brand"."""

    def test_placeholder_brand_falls_back_to_display_name(self):
        from services import menu_service

        profile = SimpleNamespace(
            brand=SimpleNamespace(name="Brand"), display_name="QA Test Store"
        )
        with patch("services.menu_catalog.get_profile_for", return_value=profile):
            self.assertEqual(menu_service._get_brand_name("919999999999"), "QA Test Store")
            header = menu_service.personalize_header("919999999999", "b2c")
        self.assertIn("QA Test Store", header)
        self.assertNotIn("Welcome to Brand", header)

    def test_no_resolvable_name_uses_neutral_greeting(self):
        from services import menu_service

        profile = SimpleNamespace(
            brand=SimpleNamespace(name="Brand"), display_name=""
        )
        with patch("services.menu_catalog.get_profile_for", return_value=profile):
            name = menu_service._get_brand_name("919999999999")
            header = menu_service.personalize_header("919999999999", "b2c")
        self.assertNotEqual(name, "Brand")
        self.assertNotIn("Welcome to Brand", header)


class SuperAdminAccountGuardTests(_BatchBase):
    """Fix 7 (D-05) — super admins administer platform accounts only."""

    def setUp(self):
        super().setUp()
        from routes import auth as auth_routes

        self.auth = auth_routes
        self.client = self._client(auth_routes.router)

    def _reset(self, caller: str, target: str):
        return self.client.post(
            f"/api/auth/admins/{target}/reset-password",
            headers=self._h(caller),
            json={"new_password": "N3w!Passw0rdzz"},
        )

    def _password_is(self, username: str, plain: str) -> bool:
        record = database.get_admin_by_username(username)
        return self.auth._verify_password(plain, record["password_hash"])

    def test_super_cannot_reset_tenant_user_password(self):
        r = self._reset("qa_super", "qa_tenant_sub")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertTrue(self._password_is("qa_tenant_sub", STRONG_PASSWORD))
        self.assertFalse(self._password_is("qa_tenant_sub", "N3w!Passw0rdzz"))

    def test_super_can_reset_platform_account_password(self):
        r = self._reset("qa_super", "qa_platform_admin")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self._password_is("qa_platform_admin", "N3w!Passw0rdzz"))

    def test_tenant_admin_can_reset_own_tenant_user(self):
        r = self._reset("qa_tenant_admin", "qa_tenant_sub")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self._password_is("qa_tenant_sub", "N3w!Passw0rdzz"))


class RebuildIndexGuardTests(_BatchBase):
    """Fix 8 (D-21) — tenant-scoped rebuild refused for super; global OK."""

    def setUp(self):
        super().setUp()
        from routes import admin as admin_routes

        self.client = self._client(admin_routes.router)

    def test_super_tenant_scoped_rebuild_is_403(self):
        r = self.client.post(
            "/api/admin/rebuild-index?background_tasks=true",
            headers=self._h("qa_super"),
            json={"tenant_id": TENANT},
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_super_global_rebuild_still_allowed(self):
        r = self.client.post(
            "/api/admin/rebuild-index?background_tasks=true",
            headers=self._h("qa_super"),
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_tenant_admin_rebuild_unchanged(self):
        r = self.client.post(
            "/api/admin/rebuild-index?background_tasks=true",
            headers=self._h("qa_tenant_admin"),
            json={"tenant_id": TENANT},
        )
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
