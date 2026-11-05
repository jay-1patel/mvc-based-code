"""Central error reporter (Phase 1): must log, persist, and never raise.

Adapted from the database branch's test_error_reporter.py to the Phase-1
signature report_error(provider, api, error, tenant_id=, request_id=, detail=).
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


class ErrorReporterTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "errors.db")

        import sqlite3

        @contextmanager
        def fake_ctx():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

        self._patch = patch("database.get_db_context", fake_ctx)
        self._patch.start()

        import services.error_reporter as er

        self.er = er

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_report_error_logs_api_error_tag(self):
        with self.assertLogs("error_reporter", level="ERROR") as cm:
            self.er.report_error(
                "send2",
                "POST /webhook/send",
                "boom",
                detail="body",
                http_status=502,
                tenant_id="demo",
                request_id="req_ab12cd34",
            )
        self.assertTrue(
            any("API_ERROR" in line and "send2" in line and "req_ab12cd34" in line
                for line in cm.output)
        )

    def test_report_error_persists_row(self):
        self.er.report_error(
            "api",
            "POST /classify",
            ValueError("bad json"),
            tenant_id="prestige",
            request_id="req_0000ffff",
        )
        rows = self.er.list_recent_errors(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["provider"], "api")
        self.assertEqual(row["api"], "POST /classify")
        self.assertEqual(row["tenant_id"], "prestige")
        self.assertEqual(row["request_id"], "req_0000ffff")
        self.assertEqual(row["http_status"], 500)
        self.assertIn("bad json", row["message"])
        self.assertIn("ValueError", row["detail"])
        # get_error round-trip + provider filter
        self.assertEqual(self.er.get_error(row["id"])["id"], row["id"])
        self.assertEqual(len(self.er.list_recent_errors(provider="api")), 1)
        self.assertEqual(len(self.er.list_recent_errors(provider="send2")), 0)
        self.assertIsNone(self.er.get_error(99999))

    def test_report_error_never_raises_when_db_broken(self):
        def boom_ctx():
            raise RuntimeError("db gone")

        with patch("database.get_db_context", boom_ctx):
            with self.assertLogs("error_reporter", level="ERROR") as cm:
                # Must not raise even though persistence fails.
                self.er.report_error("api", "GET /x", "kaput", request_id="req_1")
        self.assertTrue(any("API_ERROR" in line for line in cm.output))

    def test_alerts_off_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ERROR_ALERTS_ENABLED", None)
            os.environ.pop("ALERT_WEBHOOK_URL", None)
            self.assertFalse(self.er._alerts_enabled())


class ApiErrorsRouteTests(unittest.TestCase):
    def test_errors_route_requires_auth(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from routes.api_errors import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/api/admin/errors")
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
