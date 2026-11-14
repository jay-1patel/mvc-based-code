"""Regression tests for the E2E defect batch (2026-11-14):

1. D-E2E-6b — price questions must surface the stored product price.
2. D-E2E-7  — extraction-schema placeholder text must never publish.
3. D-E2E-6  — crawl approve must write each FAQ exactly once (D-08).
4. D-E2E-8  — a rejected product must not stay live in the products table.
5. D-E2E-1  — menu action ``track_order`` must not be remapped to offerings.
6. D-E2E-2  — list price (MRP) must survive staging alongside the price.
"""

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for _p in (BACKEND_DIR, PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_ml():
    """Install ML stubs ONLY if the real packages are absent.

    Returns the list of module names stubbed. Callers MUST pass that list
    to :func:`_unstub_ml` once the dependent module is imported: leaving
    stub modules in ``sys.modules`` defeats ImportError-based feature
    detection in unrelated code paths and perturbs other tests in the
    session (test_package_flow is sensitive to this).
    """
    class _Stub(types.ModuleType):
        def __getattr__(self, name):
            cls = type(name, (), {"__init__": lambda self, *a, **k: None})
            setattr(self, name, cls)
            return cls

    stubbed = []
    for name in ("sentence_transformers", "faiss"):
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = _Stub(name)
            stubbed.append(name)
    return stubbed


def _unstub_ml(stubbed):
    for name in stubbed or []:
        sys.modules.pop(name, None)


def _import_faq_service():
    """Import faq.service with transient ML stubs, then restore the
    session's original import environment."""
    stubbed = _stub_ml()
    try:
        import faq.service as faq_service

        return faq_service
    finally:
        _unstub_ml(stubbed)

from services.bifurcation_guards import (  # noqa: E402
    filter_faqs_for_publish,
    normalise_qa_key,
    placeholder_faq_reason,
    placeholder_offering_reason,
    placeholder_warnings,
    qa_key_from_content,
)

PLACEHOLDER_FAQ = {
    "question": "Realistic customer question?",
    "answer": "Accurate, helpful answer based on site content.",
    "category": "Pricing / Support / Booking / Policies",
}
REAL_FAQ = {
    "question": "What is the price of Trail Shoes Pro?",
    "answer": "Trail Shoes Pro costs ₹2,999.",
    "category": "Pricing",
}
PLACEHOLDER_OFFERING = {
    "name": "Product / Project / Package / Dish / Treatment Name",
    "price": "Price or Starting rate (or 'On Request')",
    "category": "Specific Category / Segment",
    "short_description": "One line summary",
    "description": "Full details",
}
TRAIL_SHOES = {
    "name": "Trail Shoes Pro",
    "category": "Footwear",
    "price": "₹2,999",
    "mrp": "₹3,499",
    "short_description": "Lightweight trail shoe",
    "description": "Trail Shoes Pro - lightweight trail shoe.",
}


# ── Fix 2: placeholder validation (unit) ────────────────────────────────────

class PlaceholderGuardTests(unittest.TestCase):
    def test_placeholder_faq_detected(self):
        self.assertIsNotNone(placeholder_faq_reason(PLACEHOLDER_FAQ))

    def test_real_faq_passes(self):
        self.assertIsNone(placeholder_faq_reason(REAL_FAQ))

    def test_filter_splits_with_visible_reasons(self):
        kept, rejected = filter_faqs_for_publish([REAL_FAQ, PLACEHOLDER_FAQ])
        self.assertEqual(kept, [REAL_FAQ])
        self.assertEqual(len(rejected), 1)
        self.assertIn("schema_placeholder", rejected[0]["reason"])
        self.assertEqual(rejected[0]["question"], PLACEHOLDER_FAQ["question"])

    def test_placeholder_offering_detected(self):
        self.assertIsNotNone(placeholder_offering_reason(PLACEHOLDER_OFFERING))
        self.assertIsNone(placeholder_offering_reason(TRAIL_SHOES))

    def test_stage_time_warnings(self):
        warnings = placeholder_warnings(
            {"faqs": [PLACEHOLDER_FAQ], "offerings": [PLACEHOLDER_OFFERING, TRAIL_SHOES]}
        )
        kinds = sorted(w["kind"] for w in warnings)
        self.assertEqual(kinds, ["faq", "offering"])


# ── Fix 3: duplicate keys (unit) ────────────────────────────────────────────

class QaDedupeKeyTests(unittest.TestCase):
    def test_formats_collide(self):
        k1 = qa_key_from_content("Q: What is X?\nA: It is Y.")
        k2 = qa_key_from_content("Question: What is X?\nAnswer: It is Y.")
        self.assertIsNotNone(k1)
        self.assertEqual(k1, k2)

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(
            normalise_qa_key("What is X?", "It is Y."),
            normalise_qa_key("what  is  x", "it is y"),
        )

    def test_different_pairs_differ(self):
        self.assertNotEqual(
            normalise_qa_key("Q1", "A1"), normalise_qa_key("Q1", "A2")
        )


# ── Fixes 5 + 6: menu action fidelity + MRP staging ─────────────────────────

class MenuAndCatalogExtractionTests(unittest.TestCase):
    def setUp(self):
        from services.auto_bifurcator import AutoBifurcator

        self.bifurcator = AutoBifurcator("demo")

    def test_track_order_survives_split_underscore(self):
        rows = [
            ["Button Title", "Description", "Section", "Action"],
            ["Shop Products", "Browse the catalogue", "Shop", "offerings"],
            ["Track Order", "Where is my order", "Support", "track order _"],
            ["Raise Ticket", "Get help", "Support", "raise ticket _"],
        ]
        items = self.bifurcator.extract_menu_section(
            {"text": "", "tables": [rows]}, vertical="ecommerce"
        )
        by_title = {i["title"]: i for i in items}
        self.assertEqual(by_title["Track Order"]["action"], "track_order")
        self.assertEqual(by_title["Raise Ticket"]["action"], "raise_ticket")
        self.assertNotIn("action_warning", by_title["Track Order"])

    def test_unknown_action_flagged_not_rewritten(self):
        rows = [
            ["Button Title", "Description", "Section", "Action"],
            ["Shop Products", "Browse the catalogue", "Shop", "offerings"],
            ["Offers", "Current offers", "Shop", "faq"],
            ["Help", "Talk to us", "Support", "book_callback"],
            ["Mystery", "Does something", "Misc", "teleport_customer"],
        ]
        items = self.bifurcator.extract_menu_section(
            {"text": "", "tables": [rows]}, vertical="ecommerce"
        )
        mystery = [i for i in items if i["title"] == "Mystery"]
        self.assertEqual(len(mystery), 1)
        self.assertEqual(mystery[0]["action"], "teleport_customer")
        self.assertIn("action_warning", mystery[0])

    def test_catalog_keeps_list_and_discount_price(self):
        rows = [
            [
                "Product Name", "SKU", "Category", "Price (₹)",
                "Discount Price", "Key Specifications / Size",
                "Stock Status", "Warranty / Returns",
            ],
            [
                "Trail Shoes Pro", "ES-SHOE-001", "Footwear", "₹3,499",
                "₹2,999", "Lightweight trail shoe", "In Stock", "7-Day Return",
            ],
        ]
        offerings = self.bifurcator.extract_catalog_section(
            {"text": "", "tables": [rows]}, vertical="ecommerce"
        )
        self.assertEqual(len(offerings), 1)
        self.assertEqual(offerings[0]["price"], "₹2,999")
        self.assertEqual(offerings[0]["mrp"], "₹3,499")


# ── Fix 1: price answers ────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


class PriceAnswerTests(unittest.TestCase):
    PRODUCTS = [
        {
            "name": "Trail Shoes Pro",
            "price": "₹2,999",
            "mrp": "₹3,499",
            "short_description": "Lightweight trail shoe",
            "is_active": 1,
        }
    ]

    def _patches(self):
        faq_service = _import_faq_service()

        return faq_service, [
            patch(
                "database.list_products",
                side_effect=lambda active_only=True, limit=100, tenant_id=None: list(self.PRODUCTS),
            ),
            patch.object(faq_service, "_brand_context", return_value={}),
            patch.object(
                faq_service, "_append_signature", side_effect=lambda a, t=None: a
            ),
        ]

    def test_deterministic_price_answer_states_stored_prices(self):
        faq_service = _import_faq_service()

        _, patches = self._patches()
        for p in patches:
            p.start()
        try:
            answer = faq_service._deterministic_price_answer(
                "What is the price of Trail Shoes Pro?", "e2estore"
            )
        finally:
            for p in patches:
                p.stop()
        self.assertIn("₹2,999", answer)
        self.assertIn("₹3,499", answer)

    def test_deterministic_answer_skips_on_request_prices(self):
        """Crawl-ingested products carry price 'On Request' — no real price
        to state, so the deterministic path must stay out of the way."""
        faq_service = _import_faq_service()

        on_request = [
            {
                "name": "A Light in the Attic",
                "price": "On Request",
                "mrp": "",
                "short_description": "Poetry collection",
                "is_active": 1,
            }
        ]
        with patch(
            "database.list_products",
            side_effect=lambda active_only=True, limit=100, tenant_id=None: list(on_request),
        ):
            answer = faq_service._deterministic_price_answer(
                "Do you have the book 'A Light in the Attic' and what is its price?",
                "e2efixstore",
            )
        self.assertEqual(answer, "")

    def test_synthesize_price_question_returns_stored_price_not_invention(self):
        """Acceptance: price question yields an answer containing ₹2,999 —
        even when the LLM would have invented a $-range instead."""
        faq_service = _import_faq_service()

        _, patches = self._patches()
        llm = patch.object(
            faq_service,
            "_generate_direct_llm_answer",
            new=AsyncMock(return_value="Trail Shoes Pro costs $100 to $200."),
        )
        retrieval = patch(
            "services.unified_retrieval.build_grounded_context",
            new=AsyncMock(return_value=("some context", {"evidence": True})),
        )
        for p in patches + [llm, retrieval]:
            p.start()
        try:
            answer = _run(
                faq_service.synthesize_intent_answer(
                    raw_content="Here are the services we offer: Trail Shoes Pro",
                    intent_name="Services",
                    user_query="What is the price of Trail Shoes Pro?",
                    tenant_id="e2estore",
                )
            )
        finally:
            for p in patches + [llm, retrieval]:
                p.stop()
        self.assertIn("₹2,999", answer)
        self.assertNotIn("$100", answer)

    def test_synthesize_rejects_invented_price_for_non_price_query(self):
        faq_service = _import_faq_service()

        _, patches = self._patches()
        llm = patch.object(
            faq_service,
            "_generate_direct_llm_answer",
            new=AsyncMock(return_value="Trail Shoes Pro costs only $500 today!"),
        )
        retrieval = patch(
            "services.unified_retrieval.build_grounded_context",
            new=AsyncMock(return_value=("Trail Shoes Pro is a shoe.", {"evidence": True})),
        )
        for p in patches + [llm, retrieval]:
            p.start()
        try:
            answer = _run(
                faq_service.synthesize_intent_answer(
                    raw_content="Trail Shoes Pro",
                    intent_name="Services",
                    user_query="Tell me about Trail Shoes Pro",
                    tenant_id="e2estore",
                )
            )
        finally:
            for p in patches + [llm, retrieval]:
                p.stop()
        self.assertEqual(answer, faq_service.UNKNOWN_ANSWER)

    def test_service_listing_includes_prices(self):
        from backend.services import service_answers

        class _Features:
            def is_on(self, flag):
                return True

        class _Intent:
            name = "service_enquiry"
            enabled = True
            keywords = ["services"]
            examples = []

        class _Profile:
            features = _Features()
            vocabulary = None

            def intent(self, name):
                return _Intent() if name == "service_enquiry" else None

            def feature_on(self, name):
                return True

        with patch(
            "shared.tenancy.loader.get_tenant_profile", return_value=_Profile()
        ), patch(
            "shared.tenancy.resolver.resolve_tenant_for_user", return_value="e2estore"
        ), patch(
            "backend.database.list_products",
            side_effect=lambda active_only, limit, tenant_id: list(self.PRODUCTS),
        ):
            reply = service_answers.answer_for_text(
                "919999999999", "What is the price of Trail Shoes Pro?",
                tenant_id="e2estore",
            )
        self.assertIsNotNone(reply)
        self.assertIn("₹2,999", reply)
        self.assertIn("₹3,499", reply)


# ── Fixes 2 + 3 + 4 + 6: publish / approve pipeline on a temp DB ────────────

class PublishPipelineTests(unittest.TestCase):
    TENANT = "e2efix"

    def setUp(self):
        import database
        from shared.tenancy import store as tenancy_store

        self.database = database
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = os.path.join(self.temp.name, "e2efix.sqlite3")
        self.clients_root = root / "clients"
        self.clients_root.mkdir()

        self.patches = [
            patch.object(database, "DB_PATH", self.db_path),
            patch("shared.tenancy.paths.CLIENTS_DIR", self.clients_root),
            patch(
                "shared.tenancy.resolver.resolve_default_tenant",
                return_value="default",
            ),
            patch.object(
                _import_faq_service().faq_index, "build", return_value=None
            ),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        tenancy_store.ensure_tenant(
            self.TENANT, slug=self.TENANT, vertical="ecommerce",
            display_name="E2E Fix Store",
        )

    def tearDown(self):
        for p in self.patches:
            try:
                p.stop()
            except Exception:
                pass
        self.temp.cleanup()

    def _faq_rows(self):
        with self.database.get_db_context() as conn:
            return conn.execute(
                "SELECT source_file, content FROM faq_dataset "
                "WHERE tenant_id = ? AND content_type = 'qa'",
                (self.TENANT,),
            ).fetchall()

    def _product_rows(self):
        with self.database.get_db_context() as conn:
            return conn.execute(
                "SELECT name, price, mrp, is_active FROM products WHERE tenant_id = ?",
                (self.TENANT,),
            ).fetchall()

    def _bifurcation(self):
        return {
            "vertical": "ecommerce",
            "brand": {"name": "E2E Fix Store", "bot_name": "Esha"},
            "offerings": [dict(TRAIL_SHOES), dict(PLACEHOLDER_OFFERING)],
            "faqs": [dict(REAL_FAQ), dict(PLACEHOLDER_FAQ)],
            "knowledge_articles": [],
            "menu_items": [],
        }

    def test_apply_rejects_placeholder_and_dedupes(self):
        from services.auto_bifurcator import AutoBifurcator

        bifurcator = AutoBifurcator(self.TENANT)
        with patch(
            "services.auto_bifurcator.BACKEND_CLIENTS_DIR", self.clients_root
        ):
            summary = bifurcator.apply_bifurcation(
                bifurcation=self._bifurcation(),
                raw_files=[],
                auto_publish=True,
                source_file="crawl_run_99.md",
            )
            # Fix 2: placeholder FAQ rejected with the count in the summary.
            self.assertEqual(summary["faqs_rejected"], 1, summary)
            self.assertEqual(summary["faqs_saved"], 1, summary)
            rows = self._faq_rows()
            self.assertEqual(len(rows), 1, rows)
            self.assertNotIn("Realistic customer question", rows[0]["content"])

            # Fix 6 + publish: product carries BOTH prices.
            products = self._product_rows()
            trail = [p for p in products if p["name"] == "Trail Shoes Pro"]
            self.assertEqual(len(trail), 1, products)
            self.assertEqual(trail[0]["price"], "₹2,999")
            self.assertEqual(trail[0]["mrp"], "₹3,499")
            self.assertEqual(trail[0]["is_active"], 1)
            # Fix 2/4: placeholder offering never became a product row.
            self.assertFalse(
                any("Treatment Name" in (p["name"] or "") for p in products),
                products,
            )

            # Fix 3: a second writer door (different source file) saving the
            # same Q&A must not duplicate it.
            summary2 = bifurcator.apply_bifurcation(
                bifurcation=self._bifurcation(),
                raw_files=[],
                auto_publish=True,
                source_file="website_faq_general.md",
            )
            self.assertEqual(summary2["faqs_saved"], 0, summary2)
            self.assertEqual(len(self._faq_rows()), 1)

    def test_product_sync_skips_rejected_offerings(self):
        from services.url_scraper import UrlAutoOnboarder

        onboarder = UrlAutoOnboarder(tenant_id=self.TENANT)
        onboarder._sync_products_with_images(
            self.TENANT, [dict(PLACEHOLDER_OFFERING), dict(TRAIL_SHOES)]
        )
        products = self._product_rows()
        names = [p["name"] for p in products]
        self.assertEqual(names, ["Trail Shoes Pro"], products)
        self.assertEqual(products[0]["mrp"], "₹3,499")

    def test_approve_writes_each_faq_exactly_once(self):
        """The D-08 regression: approve must not double-write FAQs."""
        from services.crawl_pipeline import approve_crawl_run

        run = self.database.create_crawl_run(
            self.TENANT, "http://fixture.test", trigger="manual"
        )
        payload = {
            "bifurcation": self._bifurcation(),
            "crawl": {"pages": [{"url": "http://fixture.test/"}]},
            "page_chunks": [],
        }
        self.database.save_crawl_staged(run["id"], self.TENANT, payload)
        with patch(
            "services.auto_bifurcator.BACKEND_CLIENTS_DIR", self.clients_root
        ):
            result = approve_crawl_run(
                tenant_id=self.TENANT, run_id=run["id"],
                actor={"username": "test", "role": "super_admin"},
            )
        self.assertEqual(result["status"], "published", result)
        self.assertEqual(result["faqs_created"], 1, result)
        self.assertEqual(result["faqs_rejected"], 1, result)
        rows = self._faq_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source_file"], "crawl_run_%d.md" % run["id"])
        products = self._product_rows()
        self.assertFalse(
            any("Treatment Name" in (p["name"] or "") for p in products),
            products,
        )
        self.assertEqual(result["products_live"], 1, result)


if __name__ == "__main__":
    unittest.main()
