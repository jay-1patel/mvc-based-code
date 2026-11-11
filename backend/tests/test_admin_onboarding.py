"""Admin-side onboarding wizard tests (2026-11-11).

The tenant admin completes a super admin's registration while their own
onboarding is still 'awaiting approval' (no approved change request yet —
the first-login lock is a console navigation gate; the completion path
itself must work at the API). Covers:

  * the tenant-scoped stage endpoint accepts that admin, returns the
    staged draft, and attributes the work to the real admin — not to the
    phantom super admin the old principal fallback invented;
  * tenant scoping still holds (foreign tenant stage -> 403) and a super
    admin still cannot stage into a tenant (view-only rule);
  * the full completion path the wizard submits: bind phone -> save
    draft (with the PDF menu riding in the snapshot) -> submit publish
    for approval returns 201 pending.
"""

import base64
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from routes import auth as auth_routes
from routes import onboarding_kits as kits_routes
from routes import tenant_approvals as approvals_routes
from routes import tenants as tenants_routes
from services.auto_bifurcator import AutoBifurcator
from shared.tenancy import store as tenancy_store

PASSWORD = "RootPass123!"

STAGED = {
    "status": "staged",
    "tenant_id": "leeway",
    "vertical": "it_software",
    "brand": {
        "name": "Leeway Softech",
        "bot_name": "Asha",
        "tagline": "Software that ships",
        "website": "leewaysoftech.com",
        "support_email": "support@leewaysoftech.com",
        "support_phone": "+91 98765 43210",
        "signature": "— Team Leeway",
    },
    "business_hours": {
        "timezone": "Asia/Kolkata",
        "open": "09:00",
        "close": "19:00",
        "open_days": [1, 2, 3, 4, 5, 6],
    },
    "menu_items": [
        {"id": "services", "title": "Our Services", "description": "", "section": "", "action": "services"},
        {"id": "contact", "title": "Contact Us", "description": "", "section": "", "action": "contact"},
    ],
    "offerings": [],
    "faqs": [],
    "knowledge_articles": [],
    "guardrails": {"tone": "professional and confident", "never_state": []},
    "source_files": ["company.pdf"],
    "stats": {"offerings_count": 0, "menu_items_count": 2, "faqs_count": 0, "articles_count": 0},
}


class AdminOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "onboarding.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.resolver_patch.start()
        # Same ADMIN_SECRET_KEY patch as test_role_panels (gate-env gap).
        self.key_patch = patch.object(auth_routes, "ADMIN_SECRET_KEY", "test-admin-signing-key")
        self.key_patch.start()
        database.init_db()

        self.app = FastAPI()
        if auth_routes.RATE_LIMIT_ENABLED:
            self.app.state.limiter = auth_routes.limiter
        for module in (auth_routes, tenants_routes, approvals_routes, kits_routes):
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
                "tenant_id": "leeway",
                "vertical": "it_software",
                "display_name": "Leeway",
                "mobile_number": "+91 98765 43210",
                "website": "leewaysoftech.com",
                "auto_scrape": False,
            },
            headers=self.root,
        )
        assert created.status_code == 200, created.text
        self.leeway_admin = self._create_admin("leeway-admin", tenant_id="leeway")

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

    # ── the premise: this admin really is still awaiting approval ──────

    def test_admin_is_awaiting_approval(self):
        me = self.client.get("/api/auth/me", headers=self.leeway_admin)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["role"], "admin")
        self.assertFalse(me.json()["onboarding_approved"])

    # ── stage endpoint ─────────────────────────────────────────────────

    def test_tenant_admin_can_stage_document_and_is_the_actor(self):
        captured = {}

        def fake_stage(self, files_data, lane="any"):
            captured["actor"] = dict(self.actor)
            captured["tenant_id"] = self.tenant_id
            captured["files"] = files_data
            captured["lane"] = lane
            return dict(STAGED)

        payload = {
            "files": [
                {
                    "filename": "company.pdf",
                    "content": base64.b64encode(b"%PDF-fake").decode(),
                }
            ],
            "lane": "any",
        }
        with patch.object(AutoBifurcator, "stage_files", fake_stage):
            res = self.client.post(
                "/api/admin/tenants/leeway/onboarding/stage",
                json=payload,
                headers=self.leeway_admin,
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["tenant_id"], "leeway")
        self.assertEqual(body["brand"]["name"], "Leeway Softech")
        self.assertEqual(len(body["menu_items"]), 2)
        # Attribution: the bifurcator must act as the real tenant admin,
        # not as the old fallback's phantom super admin named "admin".
        self.assertEqual(captured["actor"]["username"], "leeway-admin")
        self.assertEqual(captured["actor"]["role"], "admin")
        self.assertEqual(captured["actor"]["tenant_id"], "leeway")
        self.assertEqual(captured["tenant_id"], "leeway")

    def test_stage_for_foreign_tenant_is_refused(self):
        created = self.client.post(
            "/api/admin/tenants",
            json={
                "tenant_id": "other",
                "vertical": "generic",
                "display_name": "Other Ltd",
                "mobile_number": "+91 98765 43219",
            },
            headers=self.root,
        )
        assert created.status_code == 200, created.text
        payload = {"files": [{"filename": "x.pdf", "content": base64.b64encode(b"%PDF").decode()}]}
        with patch.object(AutoBifurcator, "stage_files", lambda self, files_data, lane="any": dict(STAGED)):
            res = self.client.post(
                "/api/admin/tenants/other/onboarding/stage",
                json=payload,
                headers=self.leeway_admin,
            )
        self.assertEqual(res.status_code, 403, res.text)

    def test_super_admin_cannot_stage_into_a_tenant(self):
        payload = {"files": [{"filename": "x.pdf", "content": base64.b64encode(b"%PDF").decode()}]}
        with patch.object(AutoBifurcator, "stage_files", lambda self, files_data, lane="any": dict(STAGED)):
            res = self.client.post(
                "/api/admin/tenants/leeway/onboarding/stage",
                json=payload,
                headers=self.root,
            )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("view-only", res.json()["detail"])

    # ── the completion path the wizard submits ─────────────────────────

    def test_awaiting_approval_admin_completion_path(self):
        bound = self.client.post(
            "/api/admin/tenants/leeway/phone-id",
            json={"waba_phone_id": "+91 8000305305"},
            headers=self.leeway_admin,
        )
        self.assertEqual(bound.status_code, 200, bound.text)

        snapshot = {
            "vertical": "it_software",
            "display_name": "Leeway",
            "brand": {
                "name": "Leeway Softech",
                "bot_name": "Asha",
                "website": "leewaysoftech.com",
                "support_email": "support@leewaysoftech.com",
                "support_phone": "+91 98765 43210",
            },
            "notifications": {"sales_email": "sales@leewaysoftech.com"},
            "menu": {
                "header": "Leeway Softech",
                "body": "Hi! I'm Asha from Leeway Softech. How can I help?",
                "buttons": [
                    {"id": "services", "title": "Our Services", "enabled": True, "sort_order": 0},
                    {"id": "contact", "title": "Contact Us", "enabled": True, "sort_order": 1},
                ],
            },
        }
        saved = self.client.put(
            "/api/admin/tenants/leeway/profile",
            json={"snapshot": snapshot},
            headers=self.leeway_admin,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["has_draft"])

        # The PDF menu rode into the saved draft snapshot.
        draft = tenancy_store.get_draft("leeway")
        self.assertIsNotNone(draft)
        button_ids = [b["id"] for b in draft["menu"]["buttons"]]
        self.assertEqual(button_ids, ["services", "contact"])

        submitted = self.client.post(
            "/api/tenant-change-requests",
            json={"request_type": "publish_profile", "tenant_id": "leeway"},
            headers=self.leeway_admin,
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)
        request = submitted.json()["request"]
        self.assertEqual(request["status"], "pending")
        self.assertEqual(request["requester_username"], "leeway-admin")
        # The request captured exactly the submitted snapshot, menu included.
        captured_menu = request["payload"]["snapshot"]["menu"]
        self.assertEqual([b["id"] for b in captured_menu["buttons"]], ["services", "contact"])


class SectionSourcesTests(unittest.TestCase):
    """Provenance labels (``section_sources``) on universal staged payloads.

    The universal extractor mixes document-derived values with vertical
    template stand-ins. These tests drive the REAL staging pipeline
    (heuristics + merge) with only the LLM transport stubbed, and check
    the labels match what the pipeline actually did:

      * a document with a parseable hours line and an LLM-extracted menu
        labels business_hours and menu "extracted";
      * a thin document (no LLM parts at all) falls back to the
        hard-coded hours and the vertical template menu — and the labels
        must say "template" for menu / business_hours / guardrails;
      * a single extracted menu item topped up with template buttons
        labels the menu "mixed".
    """

    def _stage(self, text, llm_part=None):
        bifurcator = AutoBifurcator(tenant_id="acme-docs")
        docs = [{"filename": "company.md", "text": text, "tables": []}]
        with patch.object(AutoBifurcator, "_call_llm_json", return_value=llm_part):
            return bifurcator._stage_universal(docs, text)

    def test_extracted_menu_and_hours_are_labelled_extracted(self):
        text = (
            "# Acme Corp\n\n"
            "Business Hours: 10:00 AM - 08:00 PM (Mon-Sat)\n\n"
            "Write to hello@acme.example for details.\n"
        )
        llm_part = {
            "vertical": "it_software",
            "brand": {"name": "Acme Corp", "support_email": "hello@acme.example"},
            "business_hours": {"raw": "10:00 AM - 08:00 PM (Mon-Sat)"},
            "menu_items": [
                {"id": "price_list", "title": "Price List 2026", "description": "", "section": "", "action": "pricing"},
                {"id": "book_demo", "title": "Book a Live Demo", "description": "", "section": "", "action": "demo"},
                {"id": "downloads", "title": "Downloads", "description": "", "section": "", "action": "downloads"},
            ],
            "offerings": [
                {
                    "name": "Cloud Consulting",
                    "category": "Services",
                    "price": "",
                    "short_description": "Cloud help",
                    "description": "Cloud consulting",
                }
            ],
        }
        staged = self._stage(text, llm_part)
        sources = staged["section_sources"]
        # The labels cover every section the wizard consumes.
        self.assertEqual(
            set(sources), {"brand", "business_hours", "guardrails", "menu", "offerings"}
        )
        self.assertEqual(sources["menu"], "extracted")
        self.assertEqual(sources["business_hours"], "extracted")
        self.assertEqual(sources["brand"], "extracted")
        self.assertEqual(sources["offerings"], "extracted")
        # And the labelled values really are the document's own: the
        # sanitizer parsed the raw label instead of using 09:30/18:30.
        self.assertEqual(staged["business_hours"]["open"], "10:00")
        self.assertEqual(staged["business_hours"]["close"], "20:00")
        self.assertEqual(
            [m["id"] for m in staged["menu_items"]],
            ["price_list", "book_demo", "downloads"],
        )
        # The merge never carries guardrails in universal mode, so the
        # staged rails are the template defaults — labelled as such.
        self.assertEqual(sources["guardrails"], "template")

    def test_thin_document_labels_template_fallbacks_as_template(self):
        text = "Acme Corp is a software company. Reach out to know more."
        staged = self._stage(text, llm_part=None)
        sources = staged["section_sources"]
        self.assertEqual(sources["menu"], "template")
        self.assertEqual(sources["business_hours"], "template")
        self.assertEqual(sources["guardrails"], "template")
        self.assertEqual(sources["brand"], "template")
        self.assertEqual(sources["offerings"], "template")
        # The fallbacks really are in the payload (that is the point of
        # the label): hard-coded hours and the vertical template menu.
        self.assertEqual(staged["business_hours"]["open"], "09:30")
        self.assertEqual(staged["business_hours"]["close"], "18:30")
        self.assertTrue(staged["menu_items"])
        self.assertTrue(
            all(m["id"].startswith("menu_") for m in staged["menu_items"]),
            staged["menu_items"],
        )

    def test_single_extracted_menu_item_topped_up_is_mixed(self):
        text = "# Acme Corp\n\nBusiness Hours: 10:00 AM - 08:00 PM (Mon-Sat)\n"
        llm_part = {
            "vertical": "it_software",
            "menu_items": [
                {"id": "price_list", "title": "Price List 2026", "description": "", "section": "", "action": "pricing"},
            ],
        }
        staged = self._stage(text, llm_part)
        # One document item + the vertical defaults the merge appended.
        self.assertEqual(staged["section_sources"]["menu"], "mixed")
        ids = [m["id"] for m in staged["menu_items"]]
        self.assertIn("price_list", ids)
        self.assertTrue(any(i.startswith("menu_") for i in ids))


if __name__ == "__main__":
    unittest.main()
