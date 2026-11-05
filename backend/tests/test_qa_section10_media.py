"""
QA Section 10 — Media URL resolution matrix (capture-only, no live WhatsApp).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))
sys.path.insert(0, str(ROOT / "kb"))

OUT = ROOT / "reports" / "qa"


class Section10Media(unittest.TestCase):
    def test_resolve_public_media_url_matrix(self):
        rows = []
        # Import from kb whatsapp service
        try:
            from kb.services import whatsapp as wa
        except Exception as e:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "section10_media.json").write_text(
                json.dumps({"blocked": True, "error": str(e)}), encoding="utf-8"
            )
            self.skipTest(f"whatsapp module import failed: {e}")

        fn = getattr(wa, "resolve_public_media_url", None)
        self.assertIsNotNone(fn, "resolve_public_media_url missing")

        cases = [
            ("https://cdn.example.com/a.jpg", "already_public"),
            ("/uploaded_files/kb/x.jpg", "local_path"),
            ("uploaded_files/kb/x.jpg", "bare_relative"),
            ("x.jpg", "bare_filename"),
            ("", "empty"),
            (None, "none"),
        ]
        # With PUBLIC_BASE_URL set
        with mock.patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://tunnel.example"}, clear=False):
            # also patch module-level if cached
            if hasattr(wa, "PUBLIC_BASE_URL"):
                old = wa.PUBLIC_BASE_URL
                wa.PUBLIC_BASE_URL = "https://tunnel.example"
            else:
                old = None
            try:
                for raw, label in cases:
                    try:
                        out = fn(raw)
                        rows.append({"label": label, "input": raw, "public_base": "SET", "output": out, "ok": True})
                    except Exception as e:
                        rows.append({"label": label, "input": raw, "public_base": "SET", "error": str(e), "ok": False})
            finally:
                if old is not None:
                    wa.PUBLIC_BASE_URL = old

        with mock.patch.dict(os.environ, {"PUBLIC_BASE_URL": ""}, clear=False):
            if hasattr(wa, "PUBLIC_BASE_URL"):
                wa.PUBLIC_BASE_URL = ""
            for raw, label in cases[:4]:
                try:
                    out = fn(raw)
                    rows.append({"label": label, "input": raw, "public_base": "EMPTY", "output": out, "ok": True})
                except Exception as e:
                    rows.append({"label": label, "input": raw, "public_base": "EMPTY", "error": str(e), "ok": False})

        # send_sticker intentionally disabled
        sticker = getattr(wa, "send_sticker", None)
        sticker_result = None
        if sticker:
            try:
                sticker_result = sticker("919999999999", "http://example.com/x.webp")
            except TypeError:
                try:
                    sticker_result = sticker("919999999999")
                except Exception as e:
                    sticker_result = f"error:{e}"
            except Exception as e:
                sticker_result = f"error:{e}"

        # Feature flag brochure
        brochure_notes = {}
        try:
            from backend.services import whatsapp_sender as sender
            begin = getattr(sender, "begin_capture", None)
            if begin:
                begin(capture_only=True)
                brochure_notes["capture_mode"] = "on"
        except Exception as e:
            brochure_notes["capture_error"] = str(e)

        OUT.mkdir(parents=True, exist_ok=True)
        payload = {"resolve_matrix": rows, "send_sticker_result": sticker_result, "brochure": brochure_notes}
        (OUT / "section10_media.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        # Dead relative URL with empty PUBLIC_BASE_URL is a silent-fail risk
        empty_base_locals = [
            r for r in rows if r.get("public_base") == "EMPTY" and r.get("output") and str(r["output"]).startswith("/")
        ]
        if empty_base_locals:
            (OUT / "section10_SILENT_FAIL_local_urls.json").write_text(
                json.dumps(empty_base_locals, indent=2), encoding="utf-8"
            )

        if sticker is not None:
            self.assertFalse(bool(sticker_result), "send_sticker should return False / disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
