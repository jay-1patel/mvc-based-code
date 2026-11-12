"""Extraction-fix proofs: full-text KB chunks + image capture.

Fixture site with deep body text, a repeated footer, junk pages
(privacy / login / search results), a duplicate URL spelling, srcset
images, a cross-page duplicate image, and filename-heuristic skips
(icon / sprite). Covers crawl_fulltext units and the full pipeline:
stage → export → approve.

Run with CRAWL_URL_ALLOWLIST=127.0.0.1,localhost (SSRF guard fixture).
"""
from __future__ import annotations

import io
import os
import random
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

from PIL import Image

import database
from services.crawl_fulltext import (
    build_page_knowledge,
    chunk_page,
    junk_reason_for_url,
    url_dedupe_key,
)
from shared.tenancy import store as tenancy_store


def _png_bytes(w: int, h: int, seed: int) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HERO_PNG = _png_bytes(160, 100, 1)
ALPHA_SMALL_PNG = _png_bytes(200, 120, 2)
ALPHA_LARGE_PNG = _png_bytes(400, 240, 3)
SHARED_PNG = _png_bytes(200, 150, 4)
LOGO_PNG = _png_bytes(96, 96, 5)
ICON_PNG = _png_bytes(100, 100, 6)
SPRITE_PNG = _png_bytes(120, 40, 7)

FOOTER_LINE = "Zebra footer hotline 555-0199 — printed on every page, never knowledge."
BODY_PHRASE = "Our alpha team calibrates flux capacitors every Tuesday morning"
PRIVACY_PHRASE = "We never sell your personal data to data brokers, zebras included"


def _build_site(base: str) -> dict:
    nav = f"""<nav>
      <a href="/">Home</a> <a href="/services">Services</a>
      <a href="/services/">Services (slash)</a> <a href="/about">About</a>
      <a href="/login">Login</a> <a href="/search-results">Search</a>
    </nav>"""
    footer = f"""<footer><p>{FOOTER_LINE}</p>
      <a href="/privacy-policy">Privacy</a></footer>"""

    home = f"""<!DOCTYPE html><html><head>
      <title>Deep Co — Home</title>
      <meta name="description" content="Deep Co home overview"/>
      <meta property="og:image" content="{base}/assets/hero.png"/>
      </head><body>
      <header><img src="/assets/logo.png" alt="Deep Co logo" class="logo"/></header>
      {nav}
      <h1>Deep Co builds deep things</h1>
      <p>Deep Co is a small specialist studio that designs, builds and maintains
      deep integrations for growing companies. Our home page explains who we are,
      what we value, and how engagements usually start with a short discovery
      call followed by a written proposal with honest timelines and prices.</p>
      <p>We keep teams small, senior and accountable, and we write down every
      decision so clients can audit our work at any moment without asking for
      a special report or waiting for a monthly review meeting to happen.</p>
      {footer}
      </body></html>"""

    services = f"""<!DOCTYPE html><html><head>
      <title>Our Services | Deep Co</title>
      <meta name="description" content="Services overview"/>
      </head><body>
      {nav}
      <h1>Our Services</h1>
      <h2>Alpha Service</h2>
      <p>{BODY_PHRASE} before delivering a written calibration report that
      lists every measurement, every replaced part and every recommendation
      for the next quarter of operation in plain language anyone can read.</p>
      <p>The alpha engagement includes weekly check-ins, a shared dashboard
      with live status, and a direct line to the engineer doing the work, so
      questions never wait in a ticket queue for days at a time unanswered.</p>
      <img src="/assets/alpha-small.png"
           srcset="/assets/alpha-small.png 400w, /assets/alpha-large.png 1200w"
           alt="Alpha service in action"/>
      <img src="/assets/shared.png" alt="Shared workshop photo"/>
      <img src="/assets/icon-help.png" alt="Help icon"/>
      <img src="/assets/sprite-main.png" alt="Sprite"/>
      <h2>Beta Service</h2>
      <p>Beta engagements focus on steady operational support: monitoring,
      patching, incident response and monthly reporting for systems that
      already run in production and simply need a careful, reliable team
      watching over them every single day of the year without gaps.</p>
      <p>Every beta contract includes a named deputy engineer, documented
      runbooks for the ten most common incidents, and a quarterly review
      where we retire stale alerts and rehearse the recovery procedures
      together with the client team until everyone is confident.</p>
      {footer}
      </body></html>"""

    about = f"""<!DOCTYPE html><html><head>
      <title>About Deep Co</title>
      <meta name="description" content="About Deep Co"/>
      </head><body>
      {nav}
      <h1>About Deep Co</h1>
      <p>Deep Co was founded by two engineers who believed small teams could
      out-deliver large agencies when they stay close to the work. We have
      grown slowly, hiring only when a client relationship proved it needed
      another pair of hands, and we still turn down work that does not fit.</p>
      <img src="/assets/shared-copy.png" alt="Shared workshop photo"/>
      <p>Our studio sits above a bakery, our standups are short, and our
      documentation habit is famous among clients: every project leaves with
      a handbook the client owns outright, including credentials, diagrams
      and the reasoning behind every meaningful technical decision made.</p>
      {footer}
      </body></html>"""

    privacy = f"""<!DOCTYPE html><html><head>
      <title>Privacy Policy | Deep Co</title></head><body>
      {nav}
      <h1>Privacy Policy</h1>
      <p>{PRIVACY_PHRASE}. This policy describes in great detail which
      cookies we set, how long analytics records are retained, which
      processors receive which categories of data, and how a visitor can
      request export or deletion of their records at any time.</p>
      {footer}
      </body></html>"""

    login = f"""<!DOCTYPE html><html><head>
      <title>Client Login | Deep Co</title></head><body>
      {nav}
      <h1>Client Login</h1>
      <p>Sign in with the email address your account manager invited. If you
      were locked out after too many attempts, wait fifteen minutes and try
      again, or contact support to verify your identity by phone.</p>
      {footer}
      </body></html>"""

    search = f"""<!DOCTYPE html><html><head>
      <title>Search Results | Deep Co</title></head><body>
      {nav}
      <h1>Search Results</h1>
      <p>Your search returned the following dynamically generated result
      cards. Result ranking changes with every query and this page carries
      no stable knowledge worth storing for future visitors at all.</p>
      {footer}
      </body></html>"""

    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{base}/</loc></url>
      <url><loc>{base}/services</loc></url>
      <url><loc>{base}/about</loc></url>
      <url><loc>{base}/privacy-policy</loc></url>
      <url><loc>{base}/login</loc></url>
      <url><loc>{base}/search-results</loc></url>
    </urlset>"""

    robots = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"

    return {
        "/": ("text/html", home.encode()),
        "/services": ("text/html", services.encode()),
        "/about": ("text/html", about.encode()),
        "/privacy-policy": ("text/html", privacy.encode()),
        "/login": ("text/html", login.encode()),
        "/search-results": ("text/html", search.encode()),
        "/sitemap.xml": ("application/xml", sitemap.encode()),
        "/robots.txt": ("text/plain", robots.encode()),
        "/assets/hero.png": ("image/png", HERO_PNG),
        "/assets/alpha-small.png": ("image/png", ALPHA_SMALL_PNG),
        "/assets/alpha-large.png": ("image/png", ALPHA_LARGE_PNG),
        "/assets/shared.png": ("image/png", SHARED_PNG),
        "/assets/shared-copy.png": ("image/png", SHARED_PNG),  # same bytes, other URL
        "/assets/logo.png": ("image/png", LOGO_PNG),
        "/assets/icon-help.png": ("image/png", ICON_PNG),
        "/assets/sprite-main.png": ("image/png", SPRITE_PNG),
    }


class _Handler(BaseHTTPRequestHandler):
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


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    base = f"http://127.0.0.1:{port}"
    _Handler.pages = _build_site(base)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base


# ── Unit proofs ──────────────────────────────────────────────────────────

class FulltextUnitTests(unittest.TestCase):
    def test_junk_reasons(self):
        self.assertEqual(junk_reason_for_url("https://x.test/privacy-policy"), "junk:privacy_page")
        self.assertEqual(junk_reason_for_url("https://x.test/terms-of-service"), "junk:terms_page")
        self.assertEqual(junk_reason_for_url("https://x.test/account/login"), "junk:login_page")
        self.assertEqual(junk_reason_for_url("https://x.test/listing-results"), "junk:search_results_page")
        self.assertIsNone(junk_reason_for_url("https://x.test/services"))
        self.assertIsNone(junk_reason_for_url("https://x.test/blog/privacy-tips-for-teams"))

    def test_dedupe_key_folds_spellings(self):
        a = url_dedupe_key("https://www.x.test/about/")
        b = url_dedupe_key("https://x.test/about")
        c = url_dedupe_key("https://x.test:443/about#team")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertNotEqual(url_dedupe_key("https://x.test/about"), url_dedupe_key("https://x.test/about/team"))

    def test_chunking_heading_aware_and_titled(self):
        text = (
            "## Alpha\n\n" + ("Alpha body sentence number one is here. " * 8) + "\n\n"
            "## Beta\n\n" + ("Beta body sentence follows closely behind. " * 8)
        )
        chunks = chunk_page("Services Page", text, limit=400)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["title"], "Services Page (part 1)")
        self.assertTrue(all(c["category"] == "Website page" for c in chunks))
        joined = "\n".join(c["content"] for c in chunks)
        self.assertIn("Alpha body sentence", joined)
        self.assertIn("Beta body sentence", joined)

    def test_build_knowledge_dedupes_and_skips(self):
        body = ("This page carries substantial body content for the test. " * 6)
        pages = [
            {"url": "https://x.test/services", "title": "Services", "html": "", "content": body},
            {"url": "https://x.test/services/", "title": "Services", "html": "", "content": body},
            {"url": "https://www.x.test/services", "title": "Services", "html": "", "content": body},
            {"url": "https://x.test/privacy", "title": "Privacy", "html": "", "content": body},
        ]
        kb = build_page_knowledge(pages)
        reasons = {(s["url"], s["reason"]) for s in kb["skipped"]}
        self.assertIn(("https://x.test/privacy", "junk:privacy_page"), reasons)
        self.assertEqual(sum(1 for s in kb["skipped"] if s["reason"] == "duplicate_url"), 2)
        service_chunks = [c for c in kb["chunks"] if c["url"] == "https://x.test/services"]
        self.assertTrue(service_chunks)
        self.assertIn("substantial body content", service_chunks[0]["content"])


# ── Pipeline E2E proofs ──────────────────────────────────────────────────

class FulltextPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "deep.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.clients_root = Path(self.temp.name) / "clients"
        self.clients_root.mkdir()

        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {
            "UPLOAD_DIR": str(self.upload),
            "CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost",
        })
        self.clients_patch = patch("shared.tenancy.paths.CLIENTS_DIR", self.clients_root)
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.llm_patch = patch(
            "services.url_scraper.UrlAutoOnboarder._call_llm", return_value=None
        )
        self.faq_index_patch = patch("faq.service.faq_index.build")

        def _light_faq_ingest(self_onb, tenant_id, faqs):
            return 0

        self.faq_ingest_patch = patch(
            "services.url_scraper.UrlAutoOnboarder._ingest_faqs_dataset",
            autospec=True, side_effect=_light_faq_ingest,
        )
        self.bifurcator_patch = patch(
            "services.auto_bifurcator.AutoBifurcator.apply_bifurcation",
            return_value={"tenant_id": "deep-a", "offerings_created": 0, "faqs_saved": 0, "status": "configured"},
        )
        for p in (
            self.db_patch, self.env_patch, self.clients_patch, self.resolver_patch,
            self.llm_patch, self.faq_index_patch, self.faq_ingest_patch,
            self.bifurcator_patch,
        ):
            p.start()

        database.init_db()
        tenancy_store.ensure_tenant(
            "deep-a", slug="deep-a", vertical="generic", display_name="Deep A"
        )
        self.admin = {
            "id": 1, "username": "root", "role": "super_admin",
            "tenant_id": None, "permissions": {"manage_operations": True},
            "type": "admin",
        }
        self.server, self.base = _start_server()

    def tearDown(self):
        self.server.shutdown()
        for p in (
            self.db_patch, self.env_patch, self.clients_patch, self.resolver_patch,
            self.llm_patch, self.faq_index_patch, self.faq_ingest_patch,
            self.bifurcator_patch,
        ):
            try:
                p.stop()
            except Exception:
                pass
        self.temp.cleanup()

    def _run_pipeline(self):
        from services.crawl_pipeline import run_crawl_pipeline

        run = database.create_crawl_run(
            "deep-a", self.base, trigger="manual", created_by="test"
        )
        result = run_crawl_pipeline(
            tenant_id="deep-a",
            url=self.base,
            run_id=run["id"],
            trigger="manual",
            actor=self.admin,
        )
        self.assertEqual(result["status"], "done", result)
        staged = database.get_crawl_staged(run["id"], "deep-a")
        self.assertIsNotNone(staged)
        return run, staged

    def test_stage_chunks_images_yield(self):
        run, staged = self._run_pipeline()
        payload = staged["payload"]
        chunks = payload.get("page_chunks") or []
        self.assertTrue(chunks, "no page chunks staged")

        # (a) chunk text is page BODY text, not the meta description
        all_text = "\n".join(c["content"] for c in chunks)
        self.assertIn(BODY_PHRASE, all_text)
        services_chunks = [c for c in chunks if (c["url"] or "").rstrip("/").endswith("/services")]
        self.assertTrue(services_chunks)
        self.assertTrue(services_chunks[0]["title"].endswith("(part 1)"))
        self.assertEqual(services_chunks[0]["category"], "Website page")

        # (b) repeated footer boilerplate is absent from chunks
        self.assertNotIn("Zebra footer hotline", all_text)

        # (e) junk pages skipped with reasons; their text is not staged
        yield_ = payload.get("yield") or {}
        skipped = {(s["url"].rstrip("/").rsplit("/", 1)[-1], s["reason"]) for s in yield_["pages_skipped"]}
        self.assertIn(("privacy-policy", "junk:privacy_page"), skipped)
        self.assertIn(("login", "junk:login_page"), skipped)
        self.assertIn(("search-results", "junk:search_results_page"), skipped)
        self.assertNotIn(PRIVACY_PHRASE, all_text)

        # (d) the /services + /services/ spellings stage ONE page of chunks
        services_urls = {(c["url"] or "") for c in chunks if "services" in (c["url"] or "")}
        self.assertEqual(len(services_urls), 1, services_urls)
        staged_pages = payload["crawl"]["pages"]
        svc_staged = [p for p in staged_pages if "services" in (p.get("url") or "")]
        self.assertEqual(len(svc_staged), 1, svc_staged)

        # yield numbers agree with the payload
        self.assertEqual(yield_["kb_chunks"], len(chunks))
        self.assertEqual(yield_["pages_discovered"], payload["crawl"]["pages_found"])

        # (c) images: srcset best, alt + section, hero, cross-page dedupe
        kimages = payload.get("knowledge_images") or []
        by_url = {(i["source_url"] or ""): i for i in kimages}
        alpha = next((i for u, i in by_url.items() if "alpha-large" in u), None)
        self.assertIsNotNone(alpha, f"srcset best candidate missing: {list(by_url)}")
        self.assertEqual(alpha["alt_text"], "Alpha service in action")
        self.assertEqual(alpha["section"], "Alpha Service")
        self.assertFalse(any("alpha-small" in u for u in by_url), "srcset small candidate must not be captured")
        self.assertTrue(any("hero.png" in u for u in by_url), "og hero missing")
        self.assertFalse(any("logo.png" in u for u in by_url), "logo must not be a knowledge image")
        self.assertEqual(yield_["images_captured"], len(kimages))
        self.assertGreaterEqual(yield_["images_deduped"], 1, "shared.png appears on 2 pages")

        rows = database.list_crawl_images(run["id"], "deep-a")
        icon_rows = [r for r in rows if "icon-help" in (r.get("source_url") or "")]
        self.assertTrue(icon_rows and icon_rows[0]["skipped"])
        self.assertEqual(icon_rows[0]["skip_reason"], "icon_excluded")
        sprite_rows = [r for r in rows if "sprite-main" in (r.get("source_url") or "")]
        self.assertTrue(sprite_rows and sprite_rows[0]["skip_reason"] == "sprite_excluded")
        shared_rows = [r for r in rows if "shared" in (r.get("source_url") or "")]
        self.assertEqual(sum(1 for r in shared_rows if not r["is_duplicate"] and not r["skipped"]), 1)
        self.assertGreaterEqual(sum(1 for r in shared_rows if r["is_duplicate"]), 1)

    def test_export_contains_chunks_and_image_rows(self):
        from routes import crawl as crawl_routes

        run, staged = self._run_pipeline()
        run_row = database.get_crawl_run(run["id"], "deep-a")
        images = database.list_crawl_images(run["id"], "deep-a")
        data = crawl_routes._crawl_export_data("deep-a", run["id"], run_row, staged, images=images)

        chunk_rows = [
            k for k in data["knowledge_base"]
            if (k.get("url") or "").rstrip("/").endswith("/services")
        ]
        self.assertTrue(chunk_rows, data["counts"])
        self.assertIn(BODY_PHRASE, chunk_rows[0]["content"])
        self.assertEqual(data["counts"]["images"], len(data["images"]))
        self.assertTrue(any("alpha-large" in (i["source_url"] or "") for i in data["images"]))

        csv_text = crawl_routes._crawl_export_csv(data)
        self.assertIn(BODY_PHRASE, csv_text)
        image_lines = [ln for ln in csv_text.splitlines() if ln.startswith("image,")]
        self.assertTrue(image_lines, "no image rows in CSV export")
        self.assertTrue(any("Alpha service in action" in ln for ln in image_lines))

        pdf = crawl_routes._render_crawl_export_pdf(data)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_approve_publishes_chunks_and_links_images(self):
        from services.crawl_pipeline import approve_crawl_run

        run, staged = self._run_pipeline()
        expected_images = len(staged["payload"].get("knowledge_images") or [])
        result = approve_crawl_run(tenant_id="deep-a", run_id=run["id"], actor=self.admin)
        self.assertEqual(result["status"], "published")
        self.assertGreaterEqual(result["kb_chunks_published"], 2)
        self.assertEqual(len(result["knowledge_images"]), expected_images)
        for img in result["knowledge_images"]:
            self.assertTrue(img["image_url"].startswith("/uploaded_files/product_images/"), img)
            self.assertTrue(img["source_page_url"])


if __name__ == "__main__":
    unittest.main()
