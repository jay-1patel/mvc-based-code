"""Crawl UI support: staged-knowledge export endpoint + Rule-1 approve lock.

Export: GET /api/admin/tenants/{id}/crawl-runs/{run}/export serves the
run's staged KB / FAQs / product knowledge as json | csv | pdf, with the
same tenant scoping as the sibling crawl routes.

Rule 1 (user rule, 2026-11-11): the tenant's menu, brand, business hours
and intents come ONLY from the tenant's PDF/kit. Approving a website
crawl publishes knowledge (KB, FAQs, products) and must leave those four
profile sections byte-identical.
"""
from __future__ import annotations

import json
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from shared.tenancy import store as tenancy_store


def _staged_payload() -> dict:
    """A staged crawl payload in the real pipeline shape."""
    return {
        "bifurcation": {
            "vertical": "ecommerce",
            "brand": {"name": "Website Brand Guess"},
            "menu_items": [
                {"id": "website_menu", "title": "Website Menu", "description": "x",
                 "section": "Main", "intent": "offerings", "sort_order": 1, "enabled": True},
            ],
            "business_hours": {"timezone": "Asia/Kolkata", "open": "00:00", "close": "23:59"},
            "intents": [{"name": "website_intent", "answer": "from the website"}],
            "offerings": [
                {
                    "name": "Crawled Blue Widget",
                    "category": "Widgets",
                    "price": "₹499",
                    "short_description": "A sturdy blue widget",
                    "description": "A sturdy blue widget for daily use.",
                    "media_url": "",
                    "detail_url": "https://shop.example/widgets/blue",
                },
            ],
            "faqs": [
                {
                    "question": "Do you ship nationwide?",
                    "answer": "Yes, we ship to every state within 5-7 days.",
                    "category": "Shipping",
                },
            ],
            "knowledge_articles": [
                {
                    "title": "About Crawled Co",
                    "category": "Company",
                    "markdown_content": "# About Crawled Co\n\nCrawled Co makes widgets since 1998.",
                },
            ],
        },
        "bifurcation_source": "heuristic",
        "crawl": {
            "root_url": "https://shop.example/",
            "domain": "shop.example",
            "pages_found": 2,
            "pages_scraped": 2,
            "pages": [
                {"url": "https://shop.example/", "title": "Home",
                 "meta_description": "Widgets for everyone", "word_count": 120},
                {"url": "https://shop.example/products", "title": "Products",
                 "meta_description": "", "word_count": 340},
            ],
        },
        "images_summary": {"total_rows": 0, "stored": 0, "duplicates": 0, "skipped": 0},
        "trigger": "manual",
    }


class _CrawlTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "crawl-ui.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.clients_root = Path(self.temp.name) / "clients"
        self.clients_root.mkdir()

        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {"UPLOAD_DIR": str(self.upload)})
        self.clients_patch = patch(
            "shared.tenancy.paths.CLIENTS_DIR", self.clients_root
        )
        self.bc_patch = patch(
            "services.auto_bifurcator.BACKEND_CLIENTS_DIR", self.clients_root
        )
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.env_patch.start()
        self.clients_patch.start()
        self.bc_patch.start()
        self.resolver_patch.start()
        database.init_db()

    def tearDown(self):
        for p in (
            self.db_patch, self.env_patch, self.clients_patch,
            self.bc_patch, self.resolver_patch,
        ):
            try:
                p.stop()
            except Exception:
                pass
        self.temp.cleanup()


class CrawlExportApiTests(_CrawlTestBase):
    def setUp(self):
        super().setUp()
        tenancy_store.ensure_tenant(
            "crawl-a", slug="crawl-a", vertical="ecommerce", display_name="Crawl A"
        )
        tenancy_store.ensure_tenant(
            "crawl-b", slug="crawl-b", vertical="ecommerce", display_name="Crawl B"
        )
        self.run = database.create_crawl_run(
            "crawl-a", "https://shop.example/", trigger="manual", created_by="test"
        )
        database.save_crawl_staged(self.run["id"], "crawl-a", _staged_payload())
        database.update_crawl_run(self.run["id"], status="done", mark_finished=True)
        self.empty_run = database.create_crawl_run(
            "crawl-a", "https://empty.example/", trigger="manual", created_by="test"
        )

        from routes import crawl as crawl_routes

        self.app = FastAPI()
        self.app.include_router(crawl_routes.router)
        self.client = TestClient(self.app)
        principal = {
            "id": 1, "username": "root", "role": "super_admin",
            "tenant_id": None, "permissions": {"manage_operations": True},
            "type": "admin",
        }

        def _fake_principal():
            return dict(principal)

        for route in self.app.routes:
            dependant = getattr(route, "dependant", None)
            if not dependant:
                continue
            for dep in dependant.dependencies:
                if dep.call is not None:
                    self.app.dependency_overrides[dep.call] = _fake_principal

    def _export(self, tenant: str, run_id: int, fmt: str | None = None):
        url = f"/api/admin/tenants/{tenant}/crawl-runs/{run_id}/export"
        if fmt is not None:
            url += f"?format={fmt}"
        return self.client.get(url)

    def test_export_json_happy_path(self):
        r = self._export("crawl-a", self.run["id"], "json")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("application/json", r.headers["content-type"])
        self.assertIn("attachment", r.headers["content-disposition"])
        body = r.json()
        self.assertEqual(body["counts"], {
            "knowledge_base": 1, "faqs": 1, "products": 1, "pages": 2,
        })
        self.assertEqual(body["knowledge_base"][0]["title"], "About Crawled Co")
        self.assertIn("makes widgets", body["knowledge_base"][0]["content"])
        self.assertEqual(body["faqs"][0]["question"], "Do you ship nationwide?")
        self.assertEqual(body["products"][0]["name"], "Crawled Blue Widget")
        self.assertEqual(body["products"][0]["price"], "₹499")
        # Rule 1: website menu/brand/hours guesses are NOT exported as content
        self.assertNotIn("menu_items", body)
        self.assertNotIn("brand", body)

    def test_export_default_format_is_json(self):
        r = self._export("crawl-a", self.run["id"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("application/json", r.headers["content-type"])

    def test_export_csv_happy_path(self):
        r = self._export("crawl-a", self.run["id"], "csv")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/csv", r.headers["content-type"])
        text = r.text
        self.assertIn("section,title_or_question", text)
        self.assertIn("Do you ship nationwide?", text)
        self.assertIn("Crawled Blue Widget", text)
        self.assertIn("About Crawled Co", text)
        self.assertIn("https://shop.example/products", text)

    def test_export_pdf_happy_path(self):
        r = self._export("crawl-a", self.run["id"], "pdf")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("application/pdf", r.headers["content-type"])
        self.assertTrue(r.content.startswith(b"%PDF"), r.content[:20])

    def test_export_unknown_format_400(self):
        r = self._export("crawl-a", self.run["id"], "xml")
        self.assertEqual(r.status_code, 400, r.text)

    def test_export_cross_tenant_404(self):
        r = self._export("crawl-b", self.run["id"], "json")
        self.assertEqual(r.status_code, 404, r.text)

    def test_export_unknown_run_404(self):
        r = self._export("crawl-a", 999999, "json")
        self.assertEqual(r.status_code, 404, r.text)

    def test_export_run_without_staged_payload_404(self):
        r = self._export("crawl-a", self.empty_run["id"], "json")
        self.assertEqual(r.status_code, 404, r.text)


class CrawlApproveRule1Tests(_CrawlTestBase):
    """Real apply_bifurcation (NOT mocked): crawl approval must publish
    knowledge while the PDF-owned profile sections stay byte-identical."""

    LOCKED_KEYS = ("menu", "brand", "business_hours", "intents")

    def setUp(self):
        super().setUp()
        self.faq_index_patch = patch("faq.service.faq_index.build")
        self.faq_index_patch.start()

        def _light_faq_ingest(self_onb, tenant_id, faqs):
            if not faqs:
                return 0
            count = 0
            with database.get_db_context() as conn:
                for f in faqs:
                    q = (f.get("question") or "").strip()
                    a = (f.get("answer") or "").strip()
                    if not q or not a:
                        continue
                    conn.execute(
                        """INSERT INTO faq_dataset
                               (source_file, content_type, content, page_number, module, tenant_id, created_at)
                           VALUES (?, 'qa', ?, 1, 'faq', ?, CURRENT_TIMESTAMP)""",
                        ("website_faq_test.md", f"Question: {q}\nAnswer: {a}", tenant_id),
                    )
                    count += 1
            return count

        self.faq_ingest_patch = patch(
            "services.url_scraper.UrlAutoOnboarder._ingest_faqs_dataset",
            autospec=True,
            side_effect=_light_faq_ingest,
        )
        self.faq_ingest_patch.start()

        # ── Live tenant whose profile came from its PDF ──
        tenancy_store.ensure_tenant(
            "rule1", slug="rule1", vertical="ecommerce",
            display_name="Rule One Retail",
        )
        tenancy_store.save_draft("rule1", {
            "display_name": "Rule One Retail",
            "vertical": "ecommerce",
            "brand": {
                "name": "Rule One Retail",
                "bot_name": "RuleOne Bot",
                "tagline": "From the PDF",
                "website": "https://rule-one.example",
                "support_email": "help@rule-one.example",
                "support_phone": "+91 9000000001",
                "signature": "Rule One",
            },
            "menu": {
                "key": "kb_main",
                "header": "Rule One Retail",
                "body": "Welcome from the PDF",
                "footer": "Pick one",
                "button_text": "Select Option",
                "buttons": [
                    {"id": "pdf_services", "title": "PDF Services",
                     "description": "From the PDF", "section": "Main",
                     "intent": "offerings", "sort_order": 1, "enabled": True},
                ],
            },
            "business_hours": {
                "timezone": "Asia/Kolkata",
                "always_open": False,
                "open": "08:30",
                "close": "21:30",
                "open_days": [1, 2, 3, 4, 5],
                "out_of_hours_message": "PDF hours message",
            },
            "intents": [
                {"name": "pdf_intent", "examples": ["pdf question"],
                 "keywords": ["pdf"], "answer": "Answer from the PDF",
                 "enabled": True},
            ],
        })
        draft = tenancy_store.get_draft("rule1")
        tenancy_store.publish_version("rule1", draft, published_by="test")
        tenancy_store.invalidate("rule1")

        self.actor = {
            "id": 2, "username": "rule1-admin", "role": "admin",
            "tenant_id": "rule1",
            "permissions": {"manage_operations": True}, "type": "admin",
        }

    def tearDown(self):
        for p in (self.faq_index_patch, self.faq_ingest_patch):
            try:
                p.stop()
            except Exception:
                pass
        super().tearDown()

    def _locked_snapshot(self) -> dict:
        payload = tenancy_store.get_current_payload("rule1")
        self.assertIsNotNone(payload)
        return {
            key: json.dumps(payload.get(key), sort_keys=True, default=str)
            for key in self.LOCKED_KEYS
        }

    def test_approve_publishes_knowledge_only(self):
        from services.crawl_pipeline import approve_crawl_run

        before = self._locked_snapshot()
        # Sanity: the snapshot really holds the PDF-owned values, so the
        # byte-identity assertion below is meaningful.
        self.assertIn("Rule One Retail", before["brand"])
        self.assertIn("pdf_services", before["menu"])
        self.assertIn("pdf_intent", before["intents"])
        self.assertIn("08:30", before["business_hours"])

        run = database.create_crawl_run(
            "rule1", "https://shop.example/", trigger="manual", created_by="test"
        )
        database.save_crawl_staged(run["id"], "rule1", _staged_payload())
        database.update_crawl_run(run["id"], status="done", mark_finished=True)

        result = approve_crawl_run(
            tenant_id="rule1", run_id=run["id"], actor=self.actor
        )
        self.assertEqual(result["status"], "published")
        self.assertEqual(
            sorted(result.get("rule1_locked_sections") or []),
            sorted(self.LOCKED_KEYS),
        )

        # The four PDF-owned sections are byte-identical after approval.
        after = self._locked_snapshot()
        self.assertEqual(before, after)

        # The tenant row was not renamed by the website brand guess either.
        row = tenancy_store.get_tenant("rule1") or {}
        self.assertEqual(row.get("display_name"), "Rule One Retail")

        # …while the knowledge DID land: product, FAQ and KB chunks.
        products = database.list_products(tenant_id="rule1")
        self.assertIn(
            "Crawled Blue Widget", [p.get("name") for p in products]
        )
        with database.get_db_context() as conn:
            faq_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM faq_dataset "
                "WHERE tenant_id = ? AND content LIKE '%Do you ship nationwide?%'",
                ("rule1",),
            ).fetchone()["c"]
            kb_rows = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_base WHERE tenant_id = ?",
                ("rule1",),
            ).fetchone()["c"]
        self.assertGreaterEqual(faq_rows, 1)
        self.assertGreaterEqual(kb_rows, 1)

        staged = database.get_crawl_staged(run["id"], "rule1")
        self.assertEqual(staged["status"], "published")


if __name__ == "__main__":
    unittest.main()
