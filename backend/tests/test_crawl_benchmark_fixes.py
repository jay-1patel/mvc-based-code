"""Tests for the 2026-11-12 crawler health-check fixes.

Covers: JS render trigger (b) (low text + data-rich source), canonical
(post-redirect) dedupe + scheme/index identity folding, CSS-background
image candidates, removal of the 100-image storage cap, logo-vs-icon
heuristic, card-grid extraction supplement, factual-line protection in
the boilerplate stripper, detail-first fetch order, and the
timeout-retry that keeps one slow page from killing a run.
"""
from __future__ import annotations

import io
import os
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

import httpx
from PIL import Image

import database
from services import crawl_images as crawl_images_mod
from services import site_crawler as site_crawler_mod
from services.crawl_fulltext import (
    _drop_repeated_lines,
    build_page_knowledge,
    supplement_blocks,
    url_dedupe_key,
)
from services.crawl_images import collect_image_candidates, download_and_store_crawl_images
from services.site_crawler import SiteCrawler, url_fetch_priority
from services.url_scraper import (
    html_is_data_rich,
    should_render_page,
    visible_text_word_count,
)
from shared.tenancy import store as tenancy_store


# ── Fixture pages ────────────────────────────────────────────────────────

ARTICLE = (
    "The blue widget is our most popular workshop tool. It is machined "
    "from a single block of anodised aluminium and finished by hand. "
    "Every widget passes a twelve point quality inspection before it "
    "leaves the factory floor and ships to customers worldwide."
)

CARD_BLOCKS = (
    "Precision calibration service included with every annual plan tier\n\n"
    "Dedicated onboarding specialist for teams of five or more people\n\n"
    "Priority support window with a guaranteed four hour response time"
)


def _page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def _build_pages(base: str) -> dict:
    home = _page("Home", f"""
        <h1>Widget Co</h1>
        <p>{ARTICLE}</p>
        <a href="{base}/category/widgets">All widgets</a>
        <a href="{base}/products/blue-widget">Blue widget</a>
        <a href="{base}/target">Target page</a>
        <a href="{base}/alias">Target via alias</a>
        <a href="{base}/index.html">Home spelling two</a>
    """)
    order_home = _page("Order Home", f"""
        <h1>Order Test</h1>
        <p>{ARTICLE}</p>
        <a href="{base}/category/widgets">Category first</a>
        <a href="{base}/products/blue-widget">Detail second</a>
    """)
    js_data = (
        "<html><head><title>JS Data</title></head><body>"
        "<div id='app'></div>"
        "<p>Quotes to Scrape Login</p>"
        "<script>var data = ["
        + ",".join(
            '{"tags": ["life", "truth"], "author": {"name": "Author %d", '
            '"slug": "author-%d"}, "text": "A quote about life and truth '
            'number %d that visitors love to read and share."}' % (i, i, i)
            for i in range(12)
        )
        + "];</script>"
        "</body></html>"
    )
    return {
        "/": home,
        "/index.html": home,
        "/order-home": order_home,
        "/target": _page("Target", f"<h1>Target</h1><p>{ARTICLE}</p><p>{ARTICLE}</p>"),
        "/category/widgets": _page(
            "Widgets", f"<h1>Widgets</h1><p>{ARTICLE}</p>"
            f"<a href='{base}/products/blue-widget'>Blue widget</a>"
        ),
        "/products/blue-widget": _page(
            "Blue Widget", f"<h1>Blue Widget</h1><p>{ARTICLE}</p><p>{ARTICLE}</p>"
        ),
        "/js-data": js_data,
    }


class _Handler(BaseHTTPRequestHandler):
    pages: dict = {}
    redirects: dict = {"/alias": "/target"}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in self.redirects:
            self.send_response(301)
            self.send_header("Location", self.redirects[path])
            self.end_headers()
            return
        if path.startswith("/img/") and path.endswith(".png"):
            n = int(path.split("/")[2].split(".")[0])
            img = Image.effect_noise((64, 64), 24 + (n % 40)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = self.pages.get(path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"not found")
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    base = f"http://127.0.0.1:{port}"
    _Handler.pages = _build_pages(base)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, base


class _CrawlerTestBase(unittest.TestCase):
    def setUp(self):
        self.server, self.base = _start_server()
        self.env_patch = patch.dict(os.environ, {
            "CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost",
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.server.shutdown()

    def _crawl(self, path="/", **kw):
        kw.setdefault("request_delay", 0)
        kw.setdefault("respect_robots", False)
        return SiteCrawler(f"{self.base}{path}", **kw).crawl()


# ── Render trigger ───────────────────────────────────────────────────────

class RenderTriggerTests(unittest.TestCase):
    def test_low_text_data_rich_triggers_render(self):
        # The quotes/js shape: 2 script tags, almost no visible text,
        # content embedded as a JSON-ish script payload.
        server, base = _start_server()
        try:
            import urllib.request
            with urllib.request.urlopen(f"{base}/js-data", timeout=5) as resp:
                html = resp.read().decode()
        finally:
            server.shutdown()
        self.assertLess(visible_text_word_count(html), 30)
        self.assertTrue(html_is_data_rich(html))
        self.assertTrue(should_render_page(html))

    def test_normal_article_does_not_trigger(self):
        html = _page("Article", f"<p>{ARTICLE}</p><p>{ARTICLE}</p>")
        self.assertFalse(should_render_page(html))

    def test_classic_shell_still_triggers(self):
        scripts = "".join(f"<script src='/static/app{i}.js'></script>" for i in range(6))
        html = f"<html><head>{scripts}</head><body><div id='root'></div></body></html>"
        self.assertTrue(should_render_page(html))

    def test_thin_plain_page_does_not_trigger(self):
        html = _page("Thin", "<p>hello world</p>")
        self.assertFalse(html_is_data_rich(html))
        self.assertFalse(should_render_page(html))


# ── Canonical identity / dedupe ──────────────────────────────────────────

class CanonicalIdentityTests(unittest.TestCase):
    def test_scheme_folded(self):
        self.assertEqual(
            url_dedupe_key("http://example.com/about"),
            url_dedupe_key("https://example.com/about"),
        )

    def test_default_document_folded(self):
        self.assertEqual(
            url_dedupe_key("https://example.com/index.html"),
            url_dedupe_key("https://example.com/"),
        )
        self.assertEqual(
            url_dedupe_key("https://example.com/docs/index.php"),
            url_dedupe_key("https://example.com/docs"),
        )

    def test_existing_folds_kept(self):
        self.assertEqual(
            url_dedupe_key("https://www.example.com/about/"),
            url_dedupe_key("https://example.com/about"),
        )


class CanonicalDedupeCrawlTests(_CrawlerTestBase):
    def test_redirect_alias_recorded_not_staged(self):
        res = self._crawl("/")
        staged_urls = [p["url"] for p in res["pages"]]
        target_pages = [u for u in staged_urls if u.endswith("/target")]
        self.assertEqual(len(target_pages), 1, staged_urls)
        dup_records = [
            it for it in res["inventory"]
            if it.get("error") == "canonical_duplicate"
        ]
        self.assertTrue(dup_records, res["inventory"])
        self.assertTrue(
            any((r.get("requested_url") or "").endswith("/alias") for r in dup_records),
            dup_records,
        )

    def test_index_html_home_staged_once(self):
        res = self._crawl("/")
        home_key = url_dedupe_key(f"{self.base}/")
        staged_home = [
            p for p in res["pages"] if url_dedupe_key(p["url"]) == home_key
        ]
        self.assertEqual(len(staged_home), 1, [p["url"] for p in res["pages"]])


# ── Fetch order ──────────────────────────────────────────────────────────

class FetchOrderTests(_CrawlerTestBase):
    def test_priority_function(self):
        self.assertEqual(url_fetch_priority("https://x.test/category/shoes"), 1)
        self.assertEqual(url_fetch_priority("https://x.test/page/2"), 1)
        self.assertEqual(url_fetch_priority("https://x.test/products/blue-widget"), 0)

    def test_detail_fetched_before_listing(self):
        res = self._crawl("/order-home")
        fetched = [
            it["url"] for it in res["inventory"]
            if it.get("success") and it["url"] != f"{self.base}/order-home"
        ]
        self.assertEqual(len(fetched), 2, fetched)
        self.assertTrue(fetched[0].endswith("/products/blue-widget"), fetched)
        self.assertTrue(fetched[1].endswith("/category/widgets"), fetched)


# ── Timeout retry ────────────────────────────────────────────────────────

class TimeoutRetryTests(_CrawlerTestBase):
    def test_first_timeout_retried_and_run_survives(self):
        target = f"{self.base}/target"
        real_guarded = site_crawler_mod._get_guarded
        calls = {"n": 0}

        def flaky(client, url, **kwargs):
            if url == target:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.ReadTimeout(
                        "simulated slow page",
                        request=httpx.Request("GET", url),
                    )
            return real_guarded(client, url, **kwargs)

        with patch.object(site_crawler_mod, "_get_guarded", flaky):
            res = self._crawl("/target")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(res["pages_crawled_count"], 1)
        self.assertTrue(res["pages"][0]["url"].endswith("/target"))


# ── Render plumbing (mocked renderer) ────────────────────────────────────

class RenderPlumbingTests(_CrawlerTestBase):
    def test_render_applied_and_flagged(self):
        rendered_html = _page(
            "JS Data",
            "".join(f"<p>{ARTICLE}</p>" for _ in range(3)),
        )
        with patch.object(
            site_crawler_mod, "render_with_playwright", return_value=rendered_html
        ):
            res = self._crawl("/js-data")
        self.assertEqual(res["pages_crawled_count"], 1)
        self.assertEqual(res["pages_rendered"], 1)
        self.assertTrue(res["pages"][0].get("rendered"))
        self.assertIn(f"{self.base}/js-data", res["js_rendered_urls"])
        self.assertFalse(res["render_unavailable"])

    def test_render_unavailable_recorded(self):
        with patch.object(
            site_crawler_mod, "render_with_playwright", return_value=None
        ), patch.object(
            site_crawler_mod, "playwright_available", return_value=False
        ):
            res = self._crawl("/js-data")
        self.assertTrue(res["render_unavailable"])
        self.assertEqual(res["pages_rendered"], 0)


# ── Images: CSS backgrounds, logo-vs-icon, cap removal ───────────────────

class CssBackgroundTests(unittest.TestCase):
    def test_inline_and_style_block_backgrounds_collected(self):
        html = """<html><head><style>
            .hero { background-image: url("/assets/hero-wide.jpg"); }
            .card { background: url('/assets/card-bg.png') center; }
        </style></head><body>
            <h2>Welcome</h2>
            <div class="banner" style="background-image: url('/assets/banner.webp')"></div>
            <img src="/assets/photo.jpg" alt="team"/>
        </body></html>"""
        pages = [{"url": "https://example.com/", "html": html}]
        urls = {c["source_url"] for c in collect_image_candidates(pages)}
        self.assertIn("https://example.com/assets/hero-wide.jpg", urls)
        self.assertIn("https://example.com/assets/card-bg.png", urls)
        self.assertIn("https://example.com/assets/banner.webp", urls)
        self.assertIn("https://example.com/assets/photo.jpg", urls)

    def test_inline_background_section_tracked(self):
        html = """<html><body>
            <h2>Our Headquarters</h2>
            <div style="background: url(/assets/hq.jpg)"></div>
        </body></html>"""
        pages = [{"url": "https://example.com/about", "html": html}]
        cands = [
            c for c in collect_image_candidates(pages)
            if c["source_url"].endswith("hq.jpg")
        ]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["section"], "Our Headquarters")


class LogoHeuristicTests(unittest.TestCase):
    def test_logo_under_icons_path_not_skipped(self):
        self.assertIsNone(crawl_images_mod._heuristic_skip_reason(
            "https://example.com/assets/icons/logo-animated.gif", "image", "",
        ))

    def test_logo_alt_not_skipped(self):
        self.assertIsNone(crawl_images_mod._heuristic_skip_reason(
            "https://example.com/assets/icons/brandmark.png", "image", "Company logo",
        ))

    def test_real_icon_still_skipped(self):
        self.assertEqual(
            crawl_images_mod._heuristic_skip_reason(
                "https://example.com/assets/icons/icon-search.png", "image", "",
            ),
            "icon_excluded",
        )


class ImageCapRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "cap.sqlite3")
        self.upload = Path(self.temp.name) / "uploads"
        self.upload.mkdir()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {
            "UPLOAD_DIR": str(self.upload),
            "CRAWL_URL_ALLOWLIST": "127.0.0.1,localhost",
        })
        self.resolver_patch = patch(
            "shared.tenancy.resolver.resolve_default_tenant", return_value="default"
        )
        self.db_patch.start()
        self.env_patch.start()
        self.resolver_patch.start()
        database.init_db()
        tenancy_store.ensure_tenant("cap-tenant", vertical="ecommerce", display_name="Cap")
        self.run = database.create_crawl_run(
            "cap-tenant", "http://example.test", trigger="manual"
        )
        self.server, self.base = _start_server()

    def tearDown(self):
        self.server.shutdown()
        self.db_patch.stop()
        self.env_patch.stop()
        self.resolver_patch.stop()
        self.temp.cleanup()

    def test_more_than_100_images_stored(self):
        self.assertIsNone(crawl_images_mod.MAX_IMAGES_PER_CRAWL)
        count = 105
        imgs = "".join(
            f"<img src='{self.base}/img/{i}.png' alt='pic {i}'/>" for i in range(count)
        )
        pages = [{"url": f"{self.base}/products", "html": f"<html><body>{imgs}</body></html>"}]
        results = download_and_store_crawl_images(
            tenant_id="cap-tenant", run_id=self.run["id"], pages=pages,
        )
        stored = [
            r for r in results
            if r.get("local_path") and not r.get("is_duplicate") and not r.get("skipped")
        ]
        self.assertEqual(len(stored), count, results[:3])
        self.assertFalse(
            any(r.get("skip_reason") == "max_images" for r in results)
        )


# ── Extraction quality: supplement + factual lines ───────────────────────

class SupplementTests(unittest.TestCase):
    def test_supplement_blocks_finds_missing(self):
        extracted = f"Intro paragraph. {ARTICLE}"
        blocks = f"{ARTICLE}\n\n{CARD_BLOCKS}"
        missing = supplement_blocks(extracted, blocks)
        self.assertEqual(len(missing), 3, missing)

    def test_supplement_applied_in_build_page_knowledge(self):
        base_text = " ".join(["alpha"] * 60) + " " + ARTICLE
        extra_cards = (
            CARD_BLOCKS
            + "\n\nExtended warranty cover for parts and labour up to five years"
            + "\n\nFree annual health check with a written condition report"
        )
        page = {
            "url": "https://example.com/services",
            "title": "Services",
            "html": "<html><body>ignored</body></html>",
            "content": f"{ARTICLE}\n\n{extra_cards}",
        }
        with patch(
            "services.crawl_fulltext.extract_main_text",
            return_value=(base_text, "trafilatura"),
        ):
            kb = build_page_knowledge([page])
        self.assertIn("https://example.com/services", kb["supplements"])
        joined = "\n".join(c["content"] for c in kb["chunks"])
        self.assertIn("Precision calibration service", joined)

    def test_small_supplement_not_applied(self):
        base_text = " ".join(["alpha"] * 400)
        page = {
            "url": "https://example.com/big",
            "title": "Big",
            "html": "<html><body>ignored</body></html>",
            "content": "A tiny extra block that is barely worth mentioning at all here",
        }
        with patch(
            "services.crawl_fulltext.extract_main_text",
            return_value=(base_text, "trafilatura"),
        ):
            kb = build_page_knowledge([page])
        self.assertNotIn("https://example.com/big", kb["supplements"])


class FactualLineTests(unittest.TestCase):
    def test_in_stock_and_prices_survive_boilerplate_stripping(self):
        pages = []
        for i in range(4):
            pages.append(
                f"Home | Products | Contact\n"
                f"Unique body text for page {i} with plenty of detail.\n"
                f"In stock\n"
                f"Only £25 today\n"
                f"Footer legal line repeated everywhere"
            )
        out = _drop_repeated_lines(pages)
        for text in out:
            self.assertIn("In stock", text)
            self.assertIn("Only £25 today", text)
            self.assertNotIn("Home | Products | Contact", text)
            self.assertNotIn("Footer legal line", text)


if __name__ == "__main__":
    unittest.main()
