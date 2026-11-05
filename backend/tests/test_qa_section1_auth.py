"""
QA Section 1 — Auth / admins / permissions / tenant isolation.
Evidence-producing live tests against the running server (port 9000).
Does not mutate production tenants: uses throwaway qa-* usernames when possible.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

import jwt
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))

BASE = os.getenv("QA_BASE_URL", "http://127.0.0.1:9000").rstrip("/")
REPORT = ROOT / "reports" / "qa" / "section1_auth_evidence.jsonl"


def _log(case: str, **payload):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    row = {"case": case, "ts": time.time(), **payload}
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


class Section1AuthLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Discover whether any admin exists / try common local creds from env only
        cls.admin_user = os.getenv("QA_ADMIN_USER", "")
        cls.admin_pass = os.getenv("QA_ADMIN_PASS", "")
        cls.token = None
        if cls.admin_user and cls.admin_pass:
            r = requests.post(
                f"{BASE}/api/auth/login",
                json={"username": cls.admin_user, "password": cls.admin_pass},
                timeout=15,
            )
            if r.status_code == 200:
                cls.token = r.json().get("token") or r.json().get("access_token")

    def test_login_empty_credentials(self):
        r = requests.post(f"{BASE}/api/auth/login", json={"username": "", "password": ""}, timeout=15)
        _log("login_empty", status=r.status_code, body=r.text[:500])
        self.assertIn(r.status_code, (400, 401, 422))

    def test_login_wrong_password_generic(self):
        r = requests.post(
            f"{BASE}/api/auth/login",
            json={"username": "definitely-not-a-user-xyz", "password": "wrong-password"},
            timeout=15,
        )
        body = r.text
        _log("login_wrong", status=r.status_code, body=body[:500])
        self.assertIn(r.status_code, (401, 403, 429))
        # Should not leak whether user exists
        low = body.lower()
        self.assertNotIn("no such user", low)
        self.assertNotIn("user not found", low)

    def test_login_sql_injection_style(self):
        r = requests.post(
            f"{BASE}/api/auth/login",
            json={"username": "' OR '1'='1", "password": "' OR '1'='1"},
            timeout=15,
        )
        _log("login_sqli", status=r.status_code, body=r.text[:500])
        self.assertIn(r.status_code, (401, 403, 422, 429))
        self.assertNotIn("token", r.text.lower() if r.status_code >= 400 else "")

    def test_first_admin_second_call_blocked(self):
        r1 = requests.post(
            f"{BASE}/api/auth/first-admin",
            json={"username": f"qa_first_{int(time.time())}", "password": "QaTestPass!234"},
            timeout=15,
        )
        _log("first_admin_call", status=r1.status_code, body=r1.text[:500])
        # If admins already exist, expect 403/409/400; if created, second call must fail
        r2 = requests.post(
            f"{BASE}/api/auth/first-admin",
            json={"username": f"qa_first2_{int(time.time())}", "password": "QaTestPass!234"},
            timeout=15,
        )
        _log("first_admin_second", status=r2.status_code, body=r2.text[:500])
        self.assertNotEqual(r2.status_code, 200, "second first-admin must not create another superadmin")

    def test_tampered_jwt_rejected(self):
        fake = jwt.encode({"sub": "admin", "role": "super_admin"}, "wrong-secret", algorithm="HS256")
        if isinstance(fake, bytes):
            fake = fake.decode()
        r = requests.get(f"{BASE}/api/auth/me", headers={"Authorization": f"Bearer {fake}"}, timeout=15)
        _log("tampered_jwt", status=r.status_code, body=r.text[:300])
        self.assertIn(r.status_code, (401, 403))

    def test_missing_auth_on_admin_route(self):
        r = requests.get(f"{BASE}/api/auth/admins", timeout=15)
        _log("admins_no_auth", status=r.status_code, body=r.text[:300])
        self.assertIn(r.status_code, (401, 403))

    def test_rate_limit_rapid_login(self):
        codes = []
        for i in range(25):
            r = requests.post(
                f"{BASE}/api/auth/login",
                json={"username": "rate-limit-probe", "password": f"bad-{i}"},
                timeout=15,
            )
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        _log("login_rate_limit", codes=codes)
        # Either rate limit triggers or limit is disabled — record evidence either way
        self.assertTrue(any(c in (401, 403, 429) for c in codes))

    def test_webhook_status_leaks_verify_token(self):
        r = requests.get(f"{BASE}/webhook/status", timeout=15)
        body = r.json()
        _log("webhook_status_leak", status=r.status_code, keys=list(body.keys()), has_token=bool(body.get("verify_token")))
        # Security finding if verify_token is returned unauthenticated
        if body.get("verify_token"):
            _log(
                "BUG_webhook_status_leaks_verify_token",
                severity="Major",
                evidence=f"GET /webhook/status returned verify_token without auth (len={len(str(body.get('verify_token')))})",
                suspected="backend/routes/status.py:38",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
