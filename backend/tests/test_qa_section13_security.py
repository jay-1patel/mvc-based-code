"""
QA Section 13 — security / ops probes.

These talk to the app through TestClient. Webhook status is public and must
not carry the verify token. The Send2 health check requires a super admin
and must not call Send2.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))

from routes.auth import require_super_admin  # noqa: E402
from routes.status import router as status_router  # noqa: E402

OUT = ROOT / "reports" / "qa"


class Section13Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(status_router)
        cls.app = app
        cls.client = TestClient(app)

    def test_error_bodies_no_stack_on_404(self):
        r = self.client.get("/api/this-route-does-not-exist-qa")
        body = r.text
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_404.json").write_text(
            json.dumps({"status": r.status_code, "body": body[:500]}, indent=2), encoding="utf-8"
        )
        self.assertEqual(r.status_code, 404)
        low = body.lower()
        self.assertNotIn("traceback", low)
        self.assertNotIn('file "c:\\', low)
        self.assertNotIn("password", low)

    def test_webhook_status_exposes_secrets(self):
        r = self.client.get("/webhook/status")
        data = r.json()
        findings = {
            "status": r.status_code,
            "has_verify_token": "verify_token" in data,
            "verify_token_set": data.get("verify_token_set"),
            "send2_username_set_flag": (data.get("send2_config") or {}).get("username_set"),
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_webhook_status.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
        self.assertEqual(r.status_code, 200)
        # The verify token itself must not be in a public body. Boolean
        # "password_set" / "send2_password" field names are not the secret.
        self.assertNotIn("verify_token", data)
        self.assertIn("verify_token_set", data)
        blob = json.dumps(data).lower()
        self.assertNotIn("send2_password=", blob)
        self.assertNotRegex(blob, r'"password"\s*:')

    def test_cors_headers(self):
        r = self.client.options(
            "/api/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        headers = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control")}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_cors.json").write_text(
            json.dumps({"status": r.status_code, "headers": headers}, indent=2), encoding="utf-8"
        )
        allow = headers.get("access-control-allow-origin", "")
        self.assertNotEqual(allow, "https://evil.example")

    def test_tenant_webhook_missing_secret(self):
        r = self.client.post(
            "/api/tenants/qa-nonexistent-tenant/webhook",
            json={"messages": []},
        )
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_tenant_webhook.json").write_text(
            json.dumps({"status": r.status_code, "body": r.text[:400]}, indent=2), encoding="utf-8"
        )
        self.assertIn(r.status_code, (401, 403, 404))

    def test_health_send2_reports_failure_clearly(self):
        anonymous = self.client.get("/webhook/health")
        self.assertEqual(anonymous.status_code, 401)
        self.assertNotIn("all_healthy", anonymous.json())

        self.app.dependency_overrides[require_super_admin] = lambda: {
            "role": "super_admin",
            "username": "root",
        }
        try:
            r = self.client.get("/webhook/health")
        finally:
            self.app.dependency_overrides.clear()
        data = r.json()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_health.json").write_text(json.dumps(data, indent=2)[:4000], encoding="utf-8")
        self.assertEqual(r.status_code, 200)
        self.assertIn("all_healthy", data)
        send2 = (data.get("services") or {}).get("send2_api") or {}
        self.assertIn("ok", send2)
        self.assertNotIn("password", json.dumps(data).lower())

    def test_db_path_outside_project(self):
        from routing.config import DB_PATH, PROJECT_ROOT

        row = {
            "DB_PATH": DB_PATH,
            "PROJECT_ROOT": str(PROJECT_ROOT),
            "outside_project": not str(DB_PATH).startswith(str(PROJECT_ROOT)),
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section13_db_path.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        # The suite points ROUTING_DB_PATH at a throwaway file outside the checkout.
        self.assertTrue(row["outside_project"], "Expected ROUTING_DB_PATH to point outside this checkout (current env)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
