"""Regression: staged crawl images must actually be served over HTTP.

services/crawl_images.py records each staged image as
``/crawl_images/<tenant_id>/<run_id>/<file>`` (files stored under
``<UPLOAD_DIR>/crawl_images/...``), and the Website Knowledge Images tab
renders that URL directly. Before the fix, main.py had no mount for
``/crawl_images`` so every staged image 404'd (broken-image icons in the
UI) even though the downloads themselves were fine.

These tests drive the real app: they locate the /crawl_images mount,
write a JPEG where the crawler would store it, and fetch it back through
the exact served_url shape the staged payload carries.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import unittest
from pathlib import Path

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from PIL import Image
from starlette.routing import Mount


def _load_app():
    """Import the real FastAPI app, stubbing the heavy ML deps only when
    they are genuinely absent (bare CI/VM environments); on a normal
    install the real packages import and no stub is installed."""
    import types

    class _Stub(types.ModuleType):
        def __getattr__(self, name):
            cls = type(name, (), {"__init__": lambda self, *a, **k: None})
            setattr(self, name, cls)
            return cls

    for _name in ("sentence_transformers", "faiss"):
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = _Stub(_name)
    from main import app

    return app


app = None  # loaded lazily in setUp so suite collection order is unaffected


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _crawl_images_mount_dir(application) -> Path:
    for route in application.routes:
        if isinstance(route, Mount) and route.path == "/crawl_images":
            return Path(route.app.directory)
    raise AssertionError("no /crawl_images mount registered on the app")


class TestCrawlImageServing(unittest.TestCase):
    TENANT = "servingtest"
    RUN = "run1"

    def setUp(self):
        global app
        if app is None:
            app = _load_app()
        self.app = app
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.mount_dir = _crawl_images_mount_dir(self.app)
        self.run_dir = self.mount_dir / self.TENANT / self.RUN
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.payload = _jpeg_bytes()
        (self.run_dir / "test.jpg").write_bytes(self.payload)

    def tearDown(self):
        shutil.rmtree(self.mount_dir / self.TENANT, ignore_errors=True)

    def test_mount_registered(self):
        # The staged payload's URL prefix must resolve to a real mount.
        self.assertTrue(self.mount_dir.is_dir())

    def test_staged_image_url_serves_bytes(self):
        # Same URL shape crawl_images.py stores as served_url.
        url = f"/crawl_images/{self.TENANT}/{self.RUN}/test.jpg"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, self.payload)
        self.assertTrue(resp.headers.get("content-type", "").startswith("image/"))

    def test_missing_image_404s(self):
        resp = self.client.get(f"/crawl_images/{self.TENANT}/{self.RUN}/nope.jpg")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
