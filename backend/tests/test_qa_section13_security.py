"""
QA Section 13 — security / ops probes (read-mostly; no destructive cron).
"""
from __future__ import annotations

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

BASE = os.getenv("QA_BASE_URL", "http://127.0.0.1:9000").rstrip("/")
OUT = ROOT / "reports" / "qa"


class Section13Security(unittest.TestCase):
    def test_error_bodies_no_stack_on_404(self):
        r = requests.get(f"{BASE}/api/this-route-does-not-exist-qa", timeout=15)
        body = r.text
        (OUT / "section13_404.json").write_text(
            json.dumps({"status": r.status_code, "body": body[:500]}, indent=2), encoding="utf-8"
        )
        self.assertEqual(r.status_code, 404)
        low = body.lower()
        self.assertNotIn("traceback", low)
        self.assertNotIn("file \"c:\\", low)
        self.assertNotIn("password", low)

    def test_webhook_status_exposes_secrets(self):
        r = requests.get(f"{BASE}/webhook/status", timeout=15)
        data = r.json()
        findings = {
            "status": r.status_code,
            "has_verify_token": bool(data.get("verify_token")),
            "verify_token_len": len(str(data.get("verify_token") or "")),
            "send2_username_set_flag": (data.get("send2_config") or {}).get("username_set"),
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_webhook_status.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        # This is a bug if token present without auth
        if findings["has_verify_token"]:
            (OUT / "section13_BUG_verify_token_leak.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        self.assertTrue(findings["has_verify_token"])  # document current behaviour as FAIL expectation in report

    def test_cors_headers(self):
        r = requests.options(
            f"{BASE}/api/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
            timeout=15,
        )
        headers = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control")}
        (OUT / "section13_cors.json").write_text(
            json.dumps({"status": r.status_code, "headers": headers}, indent=2), encoding="utf-8"
        )

    def test_tenant_webhook_missing_secret(self):
        # unknown tenant slug webhook
        r = requests.post(
            f"{BASE}/api/tenants/qa-nonexistent-tenant/webhook",
            json={"messages": []},
            timeout=15,
        )
        (OUT / "section13_tenant_webhook.json").write_text(
            json.dumps({"status": r.status_code, "body": r.text[:400]}, indent=2), encoding="utf-8"
        )
        self.assertIn(r.status_code, (401, 403, 404))

    def test_health_send2_reports_failure_clearly(self):
        r = requests.get(f"{BASE}/webhook/health", timeout=30)
        data = r.json()
        (OUT / "section13_health.json").write_text(json.dumps(data, indent=2)[:4000], encoding="utf-8")
        self.assertIn("all_healthy", data)
        # Evidence for send2 misconfig
        send2 = (data.get("services") or {}).get("send2_api") or {}
        self.assertIn("ok", send2)

    def test_db_path_outside_project(self):
        from routing.config import DB_PATH, PROJECT_ROOT

        row = {
            "DB_PATH": DB_PATH,
            "PROJECT_ROOT": str(PROJECT_ROOT),
            "outside_project": not str(DB_PATH).startswith(str(PROJECT_ROOT)),
        }
        (OUT / "section13_db_path.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        # Record as finding — assert documents current risky config
        self.assertTrue(row["outside_project"], "Expected ROUTING_DB_PATH to point outside this checkout (current env)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
