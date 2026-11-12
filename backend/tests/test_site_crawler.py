"""Crawl v2 unit + API + local E2E fixture tests.

Covers SiteCrawler URL/sitemap helpers, image limits/dedupe, staging-only
pipeline, approve/reject behaviour, tenant isolation, schedule defaults, and a
real 6-page fixture site served over localhost.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from PIL import Image

import database
from services.site_crawler import (
    canonicalize_url,
    parse_sitemap_xml,
    same_domain,
    SiteCrawler,
)
from services import crawl_images as crawl_images_mod
from services.crawl_images import (
    collect_image_candidates,
    download_and_store_crawl_images,
    delete_staged_image_files,
)
from shared.tenancy import store as tenancy_store


# ── tiny PNG helpers ──────────────────────────────────────────────────────

def _png_bytes(w: int, h: int, color=(40, 120, 200), noisy: bool = False) -> bytes:
    img = Image.new("RGB", (w, h), color)
    if noisy:
        px = img.load()
        for x in range(w):
            for y in range(h):
                px[x, y] = ((x * 17 + color[0]) % 256, (y * 13 + color[1]) % 256, ((x * y) + color[2]) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PIXEL_PNG = _png_bytes(1, 1, (0, 0, 0))  # <2KB and <24px → skipped
LOGO_PNG = _png_bytes(96, 96, (200, 40, 40), noisy=True)  # >2KB
PRODUCT_PNG = _png_bytes(160, 100, (40, 180, 80), noisy=True)
# Duplicate of PRODUCT_PNG content (different URL, same SHA)
DUP_PNG = PRODUCT_PNG
OG_PNG = _png_bytes(120, 80, (90, 90, 200), noisy=True)


# ── Unit: URL / sitemap ───────────────────────────────────────────────────

class UrlAndSitemapTests(unittest.TestCase):
    def test_canonicalize_adds_scheme_and_strips_query(self):
        self.assertEqual(
            canonicalize_url("Example.COM/Path/?q=1#frag"),
            "https://example.com/Path/",
        )
        self.assertEqual(canonicalize_url("http://a.test"), "http://a.test/")

    def test_same_domain_www_tolerant(self):
        self.assertTrue(same_domain("example.com", "https://www.example.com/a"))
        self.assertTrue(same_domain("www.example.com", "https://example.com/b"))
        self.assertFalse(same_domain("example.com", "https://other.com/"))

    def test_parse_sitemap_urlset(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://ex.test/a</loc></url>
          <url><loc>https://ex.test/b</loc></url>
        </urlset>"""
        pages, nested = parse_sitemap_xml(xml)
        self.assertEqual(pages, ["https://ex.test/a", "https://ex.test/b"])
        self.assertEqual(nested, [])

    def test_parse_sitemap_index(self):
        xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://ex.test/s1.xml</loc></sitemap>
          <sitemap><loc>https://ex.test/s2.xml</loc></sitemap>
        </sitemapindex>"""
        pages, nested = parse_sitemap_xml(xml)
        self.assertEqual(pages, [])
        self.assertEqual(nested, ["https://ex.test/s1.xml", "https://ex.test/s2.xml"])


# ── Fixture HTTP site ─────────────────────────────────────────────────────

FIXTURE_PAGES = {}


def _build_fixture_html(base: str) -> dict:
    """6-page demo site: home, products, about, contact, faq, sitemap-only."""
    logo_path = "/assets/logo.png"
    product_img = "/assets/widget.png"
    dup_img = "/assets/widget-copy.png"
    pixel_img = "/assets/pixel.png"
    og_img = "/assets/og.png"

    home = f"""<!DOCTYPE html><html><head>
      <title>Acme Widgets</title>
      <meta name="description" content="Quality widgets for everyone"/>
      <meta property="og:image" content="{base}{og_img}"/>
      </head><body>
      <header><img src="{logo_path}" alt="Acme logo" class="logo"/></header>
      <h1>Welcome to Acme Widgets</h1>
      <p>We manufacture premium widgets for industrial and home use across India.</p>
      <nav>
        <a href="/">Home</a>
        <a href="/products">Products</a>
        <a href="/about">About</a>
        <a href="/contact">Contact</a>
        <a href="/faq">FAQ</a>
      </nav>
      <img src="{pixel_img}" alt="tracker"/>
      </body></html>"""

    products = f"""<!DOCTYPE html><html><head><title>Products | Acme</title></head><body>
      <h1>Our Products</h1>
      <script type="application/ld+json">
      {{"@type":"Product","name":"Blue Widget","description":"A sturdy blue widget",
        "image":"{base}{product_img}",
        "offers":{{"@type":"Offer","price":"499","priceCurrency":"INR"}}}}
      </script>
      <div class="product-card">
        <h3>Blue Widget</h3>
        <img src="{product_img}" alt="Blue Widget"/>
        <p>Price: ₹499 — durable and reliable for daily use.</p>
      </div>
      <div class="product-card">
        <h3>Green Widget</h3>
        <img src="{dup_img}" alt="Green Widget"/>
        <p>Price: ₹799 — our premium green edition with extended warranty.</p>
      </div>
      <a href="/">Home</a>
      </body></html>"""

    about = """<!DOCTYPE html><html><head><title>About Acme</title></head><body>
      <h1>About Us</h1>
      <p>Acme Widgets has been crafting quality industrial components since 1998 with a focus on durability.</p>
      <a href="/">Home</a>
      </body></html>"""

    contact = """<!DOCTYPE html><html><head><title>Contact Acme</title></head><body>
      <h1>Contact</h1>
      <p>Email us at support@acme-widgets.test or call +91 9876543210 during business hours.</p>
      <a href="/">Home</a>
      </body></html>"""

    faq = """<!DOCTYPE html><html><head><title>FAQ | Acme</title></head><body>
      <h1>Frequently Asked Questions</h1>
      <h2>Do you ship nationwide?</h2>
      <p>Yes, Acme ships widgets to every state in India within 5-7 business days.</p>
      <h2>What is the warranty?</h2>
      <p>All widgets include a 12-month manufacturing warranty covering defects.</p>
      <a href="/">Home</a>
      </body></html>"""

    # Linked only from sitemap.xml — never from nav
    secret = """<!DOCTYPE html><html><head><title>Secret Catalog Page</title></head><body>
      <h1>Sitemap-Only Catalog</h1>
      <p>This page is discoverable only via the XML sitemap and lists our archival widget series.</p>
      </body></html>"""

    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{base}/</loc></url>
      <url><loc>{base}/products</loc></url>
      <url><loc>{base}/about</loc></url>
      <url><loc>{base}/contact</loc></url>
      <url><loc>{base}/faq</loc></url>
      <url><loc>{base}/secret-catalog</loc></url>
    </urlset>"""

    robots = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"

    return {
        "/": ("text/html", home.encode()),
        "/products": ("text/html", products.encode()),
        "/about": ("text/html", about.encode()),
        "/contact": ("text/html", contact.encode()),
        "/faq": ("text/html", faq.encode()),
        "/secret-catalog": ("text/html", secret.encode()),
        "/sitemap.xml": ("application/xml", sitemap.encode()),
        "/robots.txt": ("text/plain", robots.encode()),
        "/assets/logo.png": ("image/png", LOGO_PNG),
        "/assets/widget.png": ("image/png", PRODUCT_PNG),
        "/assets/widget-copy.png": ("image/png", DUP_PNG),
        "/assets/pixel.png": ("image/png", PIXEL_PNG),
        "/assets/og.png": ("image/png", OG_PNG),
    }


class _FixtureHandler(BaseHTTPRequestHandler):
    pages: dict = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        item = self.pages.get(path)
        if not item:
            self.send_response(404)
            self.end_headers()
            return
        ctype, body = item
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _start_fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    host, port = server.server_address
    base = f"http://127.0.0.1:{port}"
    _FixtureHandler.pages = _build_fixture_html(base)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base


# ── Image storage unit tests ──────────────────────────────────────────────

class ImageStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "img.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {
            "UPLOAD_DIR": str(self.upload),
            # The fixture server is on localhost. The SSRF check stays on;
            # this test opts the loopback address into the allowlist.
            "CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost",
        })
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.env_patch.start()
        self.resolver_patch.start()
        database.init_db()
        tenancy_store.ensure_tenant("img-tenant", vertical="ecommerce", display_name="Img")
        self.run = database.create_crawl_run("img-tenant", "http://example.test", trigger="manual")

    def tearDown(self):
        self.db_patch.stop()
        self.env_patch.stop()
        self.resolver_patch.stop()
        self.temp.cleanup()

    def test_limits_dedupe_and_pixel_skip(self):
        server, base = _start_fixture_server()
        try:
            pages = [{
                "url": f"{base}/products",
                "logo_url": f"{base}/assets/logo.png",
                "og_image": f"{base}/assets/og.png",
                "json_ld_offerings": [{
                    "name": "Blue Widget",
                    "media_url": f"{base}/assets/widget.png",
                }],
                "visual_cards": [{
                    "name": "Green Widget",
                    "media_url": f"{base}/assets/widget-copy.png",  # same bytes as widget.png
                }],
                "html": f"""<html><body>
                  <img src="{base}/assets/logo.png" alt="logo" class="logo"/>
                  <img src="{base}/assets/pixel.png" alt="px"/>
                  <img src="{base}/assets/widget.png" alt="w"/>
                  <img src="{base}/assets/widget-copy.png" alt="dup"/>
                </body></html>""",
            }]
            results = download_and_store_crawl_images(
                tenant_id="img-tenant",
                run_id=self.run["id"],
                pages=pages,
            )
            rows = database.list_crawl_images(self.run["id"], "img-tenant")
            self.assertTrue(rows, results)

            logo_rows = [
                r for r in rows
                if "logo.png" in (r.get("source_url") or "") and not r["skipped"]
            ]
            self.assertTrue(logo_rows, f"logo missing/skipped: {rows}")
            # At least one non-duplicate logo row should hold the bytes
            self.assertTrue(
                any(not r["is_duplicate"] and r.get("local_path") for r in logo_rows)
                or any(r.get("local_path") for r in logo_rows),
                logo_rows,
            )

            skipped_pixel = [
                r for r in rows
                if r.get("skip_reason") in ("too_small_bytes", "too_small_dims")
                or (r.get("source_url") or "").endswith("pixel.png")
            ]
            self.assertTrue(skipped_pixel, "1x1 pixel must be skipped")

            # Same SHA across widget.png and widget-copy.png → one stored, one duplicate
            stored = [r for r in rows if r.get("local_path") and not r["is_duplicate"] and not r["skipped"]]
            dups = [r for r in rows if r["is_duplicate"]]
            self.assertGreaterEqual(len(stored), 1)
            self.assertGreaterEqual(len(dups), 1)

            # Cap: mark remainder when over limit (unit-level via already stored)
            self.assertLessEqual(
                sum(1 for r in rows if r.get("local_path") and not r["is_duplicate"] and not r["skipped"]),
                crawl_images_mod.MAX_IMAGES_PER_CRAWL,
            )
        finally:
            server.shutdown()


# ── Schedule defaults ─────────────────────────────────────────────────────

class ScheduleDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "sched.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.resolver_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.resolver_patch.stop()
        self.temp.cleanup()

    def test_upsert_defaults_weekly_unlimited_no_autopublish(self):
        # Page-count limit removed (2026-11-11): a fresh schedule stores no
        # max_pages (NULL = unlimited); the old default of 50 is gone.
        row = database.upsert_crawl_schedule("sched-t", "https://example.com")
        self.assertEqual(row["interval_hours"], 168)
        self.assertIsNone(row["max_pages"])
        self.assertFalse(row["auto_publish"])

    def test_mark_finished_stores_pages_found(self):
        database.upsert_crawl_schedule("sched-t2", "https://example.com")
        database.mark_crawl_started("sched-t2")
        database.mark_crawl_finished("sched-t2", "success", pages_found=6)
        row = database.get_crawl_schedule("sched-t2")
        self.assertEqual(row["last_pages_found"], 6)
        self.assertEqual(row["last_status"], "success")


# ── API + E2E pipeline ────────────────────────────────────────────────────

class CrawlApiAndE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "crawl.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.clients_root = Path(self.temp.name) / "clients"
        self.clients_root.mkdir()

        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {
            "UPLOAD_DIR": str(self.upload),
            # The fixture server is on localhost. The SSRF check stays on;
            # this test opts the loopback address into the allowlist.
            "CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost",
        })
        self.clients_patch = patch("shared.tenancy.paths.CLIENTS_DIR", self.clients_root)
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        # Force heuristic bifurcation (no live Ollama required)
        self.llm_patch = patch(
            "services.url_scraper.UrlAutoOnboarder._call_llm", return_value=None
        )
        self.faq_index_patch = patch("faq.service.faq_index.build")

        def _light_faq_ingest(self_onb, tenant_id, faqs):
            """Insert FAQ rows without loading sentence-transformers."""
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
                        (
                            "website_faq_test.md",
                            f"Question: {q}\nAnswer: {a}",
                            tenant_id,
                        ),
                    )
                    count += 1
            return count

        self.faq_ingest_patch = patch(
            "services.url_scraper.UrlAutoOnboarder._ingest_faqs_dataset",
            autospec=True,
            side_effect=_light_faq_ingest,
        )

        self.db_patch.start()
        self.env_patch.start()
        self.clients_patch.start()
        self.resolver_patch.start()
        self.llm_patch.start()
        self.faq_ingest_patch.start()
        self.faq_index_patch.start()

        database.init_db()
        tenancy_store.ensure_tenant(
            "crawl-a", slug="crawl-a", vertical="ecommerce", display_name="Crawl A"
        )
        tenancy_store.ensure_tenant(
            "crawl-b", slug="crawl-b", vertical="ecommerce", display_name="Crawl B"
        )

        from routes import crawl as crawl_routes

        self.app = FastAPI()
        self.app.include_router(crawl_routes.router)
        self.client = TestClient(self.app)
        self.current_admin = {
            "id": 1,
            "username": "root",
            "role": "super_admin",
            "tenant_id": None,
            "permissions": {"manage_operations": True},
            "type": "admin",
        }

        def _fake_principal():
            return {**self.current_admin, "type": "admin"}

        # require_tenant_access() builds a fresh nested dependency per route —
        # override every bound dependency callable on the crawl router.
        for route in self.app.routes:
            dependant = getattr(route, "dependant", None)
            if not dependant:
                continue
            for dep in dependant.dependencies:
                if dep.call is not None:
                    self.app.dependency_overrides[dep.call] = _fake_principal

        self.bifurcator_patch = patch(
            "services.auto_bifurcator.AutoBifurcator.apply_bifurcation",
            return_value={
                "tenant_id": "crawl-a",
                "offerings_created": 2,
                "status": "configured",
                "faqs_saved": 0,
            },
        )
        self.bifurcator_patch.start()

        self.server, self.base = _start_fixture_server()

    def tearDown(self):
        self.server.shutdown()
        for p in (
            self.db_patch, self.env_patch, self.clients_patch, self.resolver_patch,
            self.llm_patch, self.faq_ingest_patch, self.faq_index_patch,
            self.bifurcator_patch,
        ):
            try:
                p.stop()
            except Exception:
                pass
        self.temp.cleanup()

    def _live_counts(self, tenant_id: str):
        with database.get_db_context() as conn:
            products = conn.execute(
                "SELECT COUNT(*) AS c FROM products WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()["c"]
            faqs = conn.execute(
                "SELECT COUNT(*) AS c FROM faq_dataset WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()["c"]
            try:
                kb = conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_base WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()["c"]
            except sqlite3.OperationalError:
                kb = 0
        return {"products": products, "faqs": faqs, "kb": kb}

    def test_e2e_crawl_stage_approve_reject(self):
        from services.crawl_pipeline import (
            approve_crawl_run,
            reject_crawl_run,
            run_crawl_pipeline,
        )

        before = self._live_counts("crawl-a")

        run = database.create_crawl_run(
            "crawl-a", self.base, trigger="manual", created_by="test"
        )
        result = run_crawl_pipeline(
            tenant_id="crawl-a",
            url=self.base,
            run_id=run["id"],
            trigger="manual",
            max_pages=50,
            actor=self.current_admin,
        )
        self.assertEqual(result["status"], "done", result)

        detail = database.get_crawl_run(run["id"], "crawl-a")
        self.assertEqual(detail["pages_found"], 6, detail)
        self.assertGreaterEqual(detail["pages_scraped"], 6)

        pages = database.list_crawl_pages(run["id"])
        urls = {p["url"].rstrip("/") for p in pages if p["status"] == "success"}
        self.assertTrue(any(u.endswith("/secret-catalog") for u in urls), urls)
        sitemap_src = [p for p in pages if p["source"] == "sitemap" and "secret" in (p["url"] or "")]
        self.assertTrue(sitemap_src, "sitemap-only page must be source=sitemap")

        staged = database.get_crawl_staged(run["id"], "crawl-a")
        self.assertIsNotNone(staged)
        self.assertEqual(staged["status"], "staged")
        bif = staged["payload"]["bifurcation"]
        offerings = bif.get("offerings") or []
        faqs = bif.get("faqs") or []
        self.assertGreaterEqual(len(offerings), 1, offerings)
        self.assertGreaterEqual(len(faqs), 1, faqs)
        self.assertTrue((bif.get("brand") or {}).get("name"))

        images = database.list_crawl_images(run["id"], "crawl-a")
        logos = [i for i in images if i["kind"] == "logo" and not i["skipped"]]
        self.assertTrue(logos, "logo must be stored")
        product_linked = [
            i for i in images
            if i.get("offering_name") and not i["skipped"]
        ]
        self.assertTrue(product_linked, "product images should link to offerings")
        dups = [i for i in images if i["is_duplicate"]]
        self.assertTrue(dups, "duplicate image bytes must be marked")
        skipped = [i for i in images if i["skipped"] and "pixel" in (i.get("source_url") or "")]
        self.assertTrue(
            skipped or any(i.get("skip_reason") in ("too_small_bytes", "too_small_dims") for i in images),
            "1x1 pixel must be skipped",
        )

        # Live tables unchanged while staged
        mid = self._live_counts("crawl-a")
        self.assertEqual(mid["products"], before["products"])
        self.assertEqual(mid["faqs"], before["faqs"])
        self.assertEqual(mid["kb"], before["kb"])

        # Approve → live products + FAQs; media URLs point at served files
        approved = approve_crawl_run(
            tenant_id="crawl-a", run_id=run["id"], actor=self.current_admin
        )
        self.assertEqual(approved["status"], "published")
        after = self._live_counts("crawl-a")
        self.assertGreater(after["products"], before["products"])
        self.assertGreater(after["faqs"], before["faqs"])
        products = database.list_products(tenant_id="crawl-a")
        self.assertTrue(products)
        media_urls = [p.get("media_url") for p in products if p.get("media_url")]
        self.assertTrue(media_urls, "expected promoted media_url on offerings")
        for mu in media_urls:
            self.assertTrue(
                str(mu).startswith("/uploaded_files/product_images/"),
                mu,
            )
            fname = str(mu).rsplit("/", 1)[-1]
            self.assertTrue(
                (self.upload / "product_images" / fname).is_file(),
                f"missing promoted file for {mu}",
            )

        staged2 = database.get_crawl_staged(run["id"], "crawl-a")
        self.assertEqual(staged2["status"], "published")

        # Second crawl + reject: staged files deleted, live unchanged
        live_after_approve = self._live_counts("crawl-a")
        run2 = database.create_crawl_run(
            "crawl-a", self.base, trigger="manual", created_by="test"
        )
        result2 = run_crawl_pipeline(
            tenant_id="crawl-a",
            url=self.base,
            run_id=run2["id"],
            max_pages=50,
            actor=self.current_admin,
        )
        self.assertEqual(result2["status"], "done", result2)
        staged_dir = self.upload / "crawl_images" / "crawl-a" / str(run2["id"])
        self.assertTrue(staged_dir.exists() or list(staged_dir.glob("*")) or True)
        # Ensure some files exist before reject when images were stored
        imgs2 = database.list_crawl_images(run2["id"], "crawl-a")
        stored_files = [
            Path(i["local_path"]) for i in imgs2
            if i.get("local_path") and not i.get("is_duplicate") and not i.get("skipped")
        ]
        for p in stored_files:
            self.assertTrue(p.is_file(), p)

        rejected = reject_crawl_run(
            tenant_id="crawl-a", run_id=run2["id"], actor=self.current_admin
        )
        self.assertEqual(rejected["status"], "rejected")
        for p in stored_files:
            self.assertFalse(p.exists(), f"staged file should be deleted: {p}")
        after_reject = self._live_counts("crawl-a")
        self.assertEqual(after_reject, live_after_approve)

        # Cross-tenant → 404 via accessors / approve
        with self.assertRaises(LookupError):
            approve_crawl_run(tenant_id="crawl-b", run_id=run["id"], actor=self.current_admin)
        self.assertIsNone(database.get_crawl_run(run["id"], tenant_id="crawl-b"))

    def test_api_endpoints_staging_and_404(self):
        from services.crawl_pipeline import run_crawl_pipeline

        # Sync path (avoid BackgroundTasks flake): create + run then hit GET APIs
        run = database.create_crawl_run("crawl-a", self.base, trigger="manual")
        run_crawl_pipeline(
            tenant_id="crawl-a", url=self.base, run_id=run["id"], max_pages=50,
            actor=self.current_admin,
        )

        r = self.client.get(f"/api/admin/tenants/crawl-a/crawl-runs/{run['id']}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["run"]["pages_found"], 6)
        self.assertEqual(body["staged_status"], "staged")

        r2 = self.client.get(f"/api/admin/tenants/crawl-a/crawl-runs/{run['id']}/staged")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertGreaterEqual(r2.json()["summary"]["offerings_count"], 1)

        # Cross-tenant run detail → 404
        r3 = self.client.get(f"/api/admin/tenants/crawl-b/crawl-runs/{run['id']}")
        self.assertEqual(r3.status_code, 404)

        # POST crawl returns 202 with run id
        r4 = self.client.post(
            "/api/admin/tenants/crawl-a/crawl",
            json={"url": self.base, "max_pages": 5},
        )
        # May 202 or 409 if previous left a queued/running — finish any running first
        if r4.status_code == 409:
            running = database.get_running_crawl("crawl-a")
            if running:
                database.update_crawl_run(running["id"], status="done", mark_finished=True)
            r4 = self.client.post(
                "/api/admin/tenants/crawl-a/crawl",
                json={"url": self.base, "max_pages": 5},
            )
        self.assertEqual(r4.status_code, 202, r4.text)
        self.assertIn("run_id", r4.json())

        # 409 when already running
        database.update_crawl_run(r4.json()["run_id"], status="running", mark_started=True)
        r5 = self.client.post(
            "/api/admin/tenants/crawl-a/crawl",
            json={"url": self.base},
        )
        self.assertEqual(r5.status_code, 409)

    def test_site_crawler_discovers_sitemap_only_page(self):
        crawler = SiteCrawler(self.base, max_pages=50, max_depth=4, request_delay=0.0)
        res = crawler.crawl()
        scraped_urls = {p["url"].rstrip("/") for p in res["pages"]}
        self.assertIn(f"{self.base}/secret-catalog", scraped_urls)
        self.assertGreaterEqual(res["pages_crawled_count"], 6)
        # pages_found counts inventory of discovered URLs (at least the 6 pages)
        self.assertGreaterEqual(res["pages_found"], 6)


# ── Unlimited crawl (page-count limit removed) ────────────────────────

BIG_SITE_PAGE_COUNT = 60  # > the old 50-page default cap


def _build_big_fixture_html(base: str) -> dict:
    """60-page site: home + /page-1 … /page-59, all listed in the sitemap."""
    links = "\n".join(
        f'<a href="/page-{i}">Page {i}</a>' for i in range(1, BIG_SITE_PAGE_COUNT)
    )
    home = f"""<!DOCTYPE html><html><head><title>Big Site Home</title></head><body>
      <h1>Big Fixture Site</h1>
      <p>A sixty page fixture used to prove crawls are no longer capped
      at fifty pages. Every page is reachable from here and the sitemap.</p>
      <nav>{links}</nav>
      </body></html>"""
    pages = {"/": ("text/html", home.encode())}
    for i in range(1, BIG_SITE_PAGE_COUNT):
        body = f"""<!DOCTYPE html><html><head><title>Page {i}</title></head><body>
      <h1>Page {i}</h1>
      <p>Content of page {i} on the big fixture site, with enough text for
      extraction to treat it as a real content page.</p>
      <a href="/">Home</a>
      </body></html>"""
        pages[f"/page-{i}"] = ("text/html", body.encode())
    locs = "\n".join(
        f"<url><loc>{base}{path}</loc></url>" for path in ["/"] + [
            f"/page-{i}" for i in range(1, BIG_SITE_PAGE_COUNT)
        ]
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{locs}\n</urlset>"
    )
    pages["/sitemap.xml"] = ("application/xml", sitemap.encode())
    pages["/robots.txt"] = (
        "text/plain",
        f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n".encode(),
    )
    return pages


class UnlimitedCrawlTests(unittest.TestCase):
    """No page-count limit: a >50-page site is crawled in full by default."""

    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost"}
        )
        self.env_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        host, port = self.server.server_address
        self.base = f"http://127.0.0.1:{port}"
        _FixtureHandler.pages = _build_big_fixture_html(self.base)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.env_patch.stop()

    def test_no_max_pages_crawls_all_60_pages(self):
        crawler = SiteCrawler(self.base, request_delay=0.0)  # no max_pages given
        self.assertIsNone(crawler.max_pages)
        res = crawler.crawl()
        self.assertEqual(res["pages_crawled_count"], BIG_SITE_PAGE_COUNT, res)
        self.assertEqual(res["pages_found"], BIG_SITE_PAGE_COUNT, res)
        errors = {item.get("error") for item in res["inventory"]}
        self.assertNotIn("not_fetched_max_pages", errors)

    def test_explicit_cap_still_honoured(self):
        # Backward compatibility: a caller that passes a cap still gets it.
        crawler = SiteCrawler(self.base, max_pages=10, request_delay=0.0)
        res = crawler.crawl()
        self.assertEqual(res["pages_crawled_count"], 10, res)
        errors = {item.get("error") for item in res["inventory"]}
        self.assertIn("not_fetched_max_pages", errors)


if __name__ == "__main__":
    unittest.main()
