"""Password strength is one rule, on every path that sets a password."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from routes import auth as auth_routes

GOOD = "Test-password1!"
WEAK = [
    ("short", "at least 12"),
    ("test-password", "uppercase"),
    ("alllowercase1!", "uppercase"),
    ("ALLUPPERCASE1!", "lowercase"),
    ("NoDigitsHere!!", "digit"),
    ("NoSpecial1234", "special"),
]


class PasswordPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            database, "DB_PATH", os.path.join(self.temp.name, "pw.sqlite3")
        )
        self.db_patch.start()
        database.init_db()
        self.app = FastAPI()
        if auth_routes.RATE_LIMIT_ENABLED:
            self.app.state.limiter = auth_routes.limiter
        self.app.include_router(auth_routes.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def _detail(self, response):
        body = response.json()
        detail = body.get("detail", body)
        if isinstance(detail, list):
            return " ".join(str(item) for item in detail)
        return str(detail)

    def test_first_admin_and_create_reject_weak_passwords(self):
        for password, needle in WEAK:
            res = self.client.post(
                "/api/auth/first-admin",
                json={"username": "root", "password": password},
            )
            self.assertEqual(res.status_code, 400, res.text)
            self.assertIn(needle, self._detail(res).lower())

        created = self.client.post(
            "/api/auth/first-admin",
            json={"username": "root", "password": GOOD, "email": "root@example.com"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post(
            "/api/auth/login", json={"username": "root", "password": GOOD}
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        for password, needle in WEAK:
            res = self.client.post(
                "/api/auth/create",
                json={
                    "username": "helper",
                    "password": password,
                    "role": "admin",
                    "email": "helper@example.com",
                },
                headers=headers,
            )
            self.assertEqual(res.status_code, 400, res.text)
            self.assertIn(needle, self._detail(res).lower())

        made = self.client.post(
            "/api/auth/create",
            json={
                "username": "helper",
                "password": GOOD,
                "role": "admin",
                "email": "helper@example.com",
            },
            headers=headers,
        )
        self.assertEqual(made.status_code, 200, made.text)

    def test_change_and_admin_reset_use_the_same_rule(self):
        self.client.post(
            "/api/auth/first-admin",
            json={"username": "root", "password": GOOD, "email": "root@example.com"},
        )
        login = self.client.post(
            "/api/auth/login", json={"username": "root", "password": GOOD}
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        self.client.post(
            "/api/auth/create",
            json={
                "username": "helper",
                "password": GOOD,
                "role": "admin",
                "email": "helper@example.com",
            },
            headers=headers,
        )

        weak = self.client.post(
            "/api/auth/change-password",
            json={"current_password": GOOD, "new_password": "short"},
            headers=headers,
        )
        self.assertEqual(weak.status_code, 400, weak.text)
        self.assertIn("at least 12", self._detail(weak).lower())

        changed = self.client.post(
            "/api/auth/change-password",
            json={"current_password": GOOD, "new_password": "Changed-pass1!"},
            headers=headers,
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        # Log in with the new password and reset the other admin.
        login = self.client.post(
            "/api/auth/login", json={"username": "root", "password": "Changed-pass1!"}
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        reset = self.client.post(
            "/api/auth/admins/helper/reset-password",
            json={"new_password": "test-password"},
            headers=headers,
        )
        self.assertEqual(reset.status_code, 400, reset.text)
        strong = self.client.post(
            "/api/auth/admins/helper/reset-password",
            json={"new_password": "Helper-pass1!"},
            headers=headers,
        )
        self.assertEqual(strong.status_code, 200, strong.text)
        helper = self.client.post(
            "/api/auth/login", json={"username": "helper", "password": "Helper-pass1!"}
        )
        self.assertEqual(helper.status_code, 200, helper.text)

    def test_otp_reset_rejects_a_weak_password_and_accepts_a_strong_one(self):
        self.client.post(
            "/api/auth/first-admin",
            json={"username": "root", "password": GOOD, "email": "root@example.com"},
        )
        otp = "654321"
        otp_hash = bcrypt.hashpw(otp.encode(), bcrypt.gensalt()).decode()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        database.set_admin_otp("root", otp_hash, expires)

        weak = self.client.post(
            "/api/auth/reset-password",
            json={"username": "root", "otp": otp, "new_password": "short"},
        )
        self.assertEqual(weak.status_code, 400, weak.text)
        self.assertIn("at least 12", self._detail(weak).lower())

        # The weak attempt must not consume the OTP.
        strong = self.client.post(
            "/api/auth/reset-password",
            json={"username": "root", "otp": otp, "new_password": "Reset-pass1!"},
        )
        self.assertEqual(strong.status_code, 200, strong.text)
        login = self.client.post(
            "/api/auth/login", json={"username": "root", "password": "Reset-pass1!"}
        )
        self.assertEqual(login.status_code, 200, login.text)


if __name__ == "__main__":
    unittest.main()
