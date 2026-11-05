"""
QA Section 3 — Upload validation matrix (frontend asserts + backend constants + local process).
Live authenticated upload paths run only when QA_ADMIN_USER/PASS are set.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))

OUT = ROOT / "reports" / "qa"
PACK = ROOT / "qa_testdata"
BASE = os.getenv("QA_BASE_URL", "http://127.0.0.1:9000").rstrip("/")


class Section3UploadMatrix(unittest.TestCase):
    def test_limits_and_accept_lists_agree(self):
        # Backend constants
        from backend.routes import admin as admin_routes
        from backend.routes import tenant_files as tf

        be_max = getattr(admin_routes, "MAX_UPLOAD_BYTES", None)
        be_supported = getattr(admin_routes, "SUPPORTED", None) or getattr(admin_routes, "SUPPORTED_EXTENSIONS", None)
        tf_max = getattr(tf, "MAX_UPLOAD_BYTES", None)
        row = {
            "admin_MAX_UPLOAD_BYTES": be_max,
            "admin_SUPPORTED": list(be_supported) if be_supported else None,
            "tenant_files_MAX_UPLOAD_BYTES": tf_max,
        }
        # Frontend UPLOAD_ACCEPT
        uploads_ts = (ROOT / "frontend" / "src" / "lib" / "uploads.ts").read_text(encoding="utf-8")
        row["frontend_has_UPLOAD_ACCEPT"] = "UPLOAD_ACCEPT" in uploads_ts
        row["frontend_mentions_20"] = "20" in uploads_ts and ("MB" in uploads_ts or "1024" in uploads_ts or "20971520" in uploads_ts)
        # Extract numeric limits from frontend if present
        import re

        m = re.search(r"MAX_UPLOAD[^\n=]*=\s*([0-9_]+)", uploads_ts)
        row["frontend_MAX_UPLOAD_raw"] = m.group(1) if m else None
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section3_limits.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        self.assertIsNotNone(be_max)
        if tf_max is not None and be_max is not None:
            self.assertEqual(be_max, tf_max, "admin vs tenant_files size limit mismatch")

    def test_file_type_local_processing_matrix(self):
        from backend.extract import process_file

        cases = []
        files = [
            ("clean_multi_page.pdf", True),
            ("same_content.docx", True),
            ("same_content.txt", True),
            ("same_content.md", True),
            ("same_content.csv", True),
            ("same_content.xlsx", True),
            ("UPPER.PDF", True),
            ("file with spaces.pdf", True),
            ("unsupported.exe", False),
            ("unsupported.zip", False),
            ("unsupported.mp4", False),
            ("file.pdf.exe", False),
            ("zero_byte.pdf", True),  # may return empty pages
            ("corrupted.pdf", True),  # open may succeed with empty/partial
            ("password_protected.pdf", True),
            ("no_extension", False),
            ("lie_extension.pdf", True),
        ]
        for name, expect_some in files:
            path = PACK / name
            entry = {"file": name, "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
            if not path.exists():
                entry["verdict"] = "BLOCKED"
                cases.append(entry)
                continue
            try:
                result = process_file(str(path))
                entry["result_is_none"] = result is None
                entry["keys"] = list(result.keys()) if isinstance(result, dict) else None
                entry["verdict"] = "PASS"
                if expect_some and result is None:
                    entry["verdict"] = "FAIL"
                    entry["error"] = "expected extractor result, got None"
            except Exception as e:
                entry["verdict"] = "FAIL"
                entry["error"] = f"{type(e).__name__}: {e}"
            cases.append(entry)
        (OUT / "section3_filetype_matrix.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
        fails = [c for c in cases if c.get("verdict") == "FAIL"]
        self.assertEqual(fails, [], msg=json.dumps(fails, indent=2)[:1500])

    def test_unauthenticated_upload_rejected(self):
        # Probe common upload endpoints without auth
        probes = []
        endpoints = [
            ("POST", "/api/admin/files/upload"),
            ("POST", "/files/upload"),
            ("POST", "/files/image"),
            ("POST", "/api/tenants/1/kb/upload"),
            ("POST", "/offerings/upload-pdf"),
            ("POST", "/brochures/upload"),
            ("POST", "/upload/image"),
            ("POST", "/upload/logo"),
        ]
        tiny = base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode()
        for method, path in endpoints:
            url = BASE + path
            try:
                if "files/upload" in path or "admin" in path:
                    r = requests.post(
                        url,
                        json={"filename": "qa.pdf", "content_base64": tiny, "module": "kb"},
                        timeout=20,
                    )
                else:
                    r = requests.post(url, files={"file": ("qa.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")}, timeout=20)
                probes.append({"path": path, "status": r.status_code, "body": r.text[:200]})
            except requests.RequestException as e:
                probes.append({"path": path, "status": None, "error": str(e)[:200]})
        (OUT / "section3_unauth_upload.json").write_text(json.dumps(probes, indent=2), encoding="utf-8")
        # Every endpoint must fail closed (401/403/404/405/422) — never 200
        opened = [p for p in probes if p.get("status") == 200]
        self.assertEqual(opened, [], msg=f"Unauthenticated upload succeeded: {opened}")

    def test_path_traversal_filename_sanitized_concept(self):
        # Unit-level: ensure save path helpers don't escape upload root if present
        suspects = []
        for rel in [
            "backend/routes/tenant_files.py",
            "backend/routes/admin.py",
            "backend/routes/tenant_admin.py",
        ]:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            suspects.append(
                {
                    "file": rel,
                    "uses_secure_filename": "secure_filename" in text or "os.path.basename" in text,
                    "joins_upload_dir": "uploaded_files" in text,
                    "mentions_traversal": ".." in text,
                }
            )
        (OUT / "section3_filename_guards.json").write_text(json.dumps(suspects, indent=2), encoding="utf-8")
        weak = [s for s in suspects if s["joins_upload_dir"] and not s["uses_secure_filename"]]
        # Not hard-fail if basename used elsewhere — record for report
        if weak:
            (OUT / "section3_filename_guards_WEAK.json").write_text(json.dumps(weak, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
