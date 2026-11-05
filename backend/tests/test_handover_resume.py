"""Regression: after escalate handoff, last message offers type-hi resume, and hi works."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from shared.handover import HANDOVER_RESUME_HINT, append_resume_hint, wants_bot_resume
from shared.tenancy import cache as tenancy_cache
from shared.tenancy import store as tenancy_store

try:
    from services import flow_runner
    from services import whatsapp_sender
except ImportError:  # pragma: no cover
    from backend.services import flow_runner
    from backend.services import whatsapp_sender


TENANT = "handover-resume-test"
WA = "918888888888"


class HandoverHelpersTest(unittest.TestCase):
    def test_exact_hi_resumes(self):
        self.assertTrue(wants_bot_resume("hi"))
        self.assertTrue(wants_bot_resume("Hi"))
        self.assertTrue(wants_bot_resume("hello"))
        self.assertTrue(wants_bot_resume("main menu"))

    def test_substring_hi_does_not_false_positive(self):
        self.assertFalse(wants_bot_resume("this"))
        self.assertFalse(wants_bot_resume("thinking"))
        self.assertFalse(wants_bot_resume("which package"))

    def test_append_resume_hint_is_idempotent(self):
        once = append_resume_hint("Thanks! Our team will call you.")
        self.assertIn(HANDOVER_RESUME_HINT, once)
        twice = append_resume_hint(once)
        self.assertEqual(once.count("Type *hi*"), 1)
        self.assertEqual(twice, once)


class EscalateShowsHiHintTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "handover.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

        tenancy_store.ensure_tenant(
            TENANT, vertical="generic", display_name="Handover Test",
        )
        tenancy_cache.purge(TENANT)
        self.capture_tokens = whatsapp_sender.begin_capture(capture_only=True)

    def tearDown(self):
        whatsapp_sender.end_capture(self.capture_tokens)
        tenancy_cache.purge(TENANT)
        self.db_patch.stop()
        self.temp.cleanup()

    def _outcome_text(self, outcome) -> str:
        return flow_runner.messages_text(outcome.get("messages") or [])

    def _finish_flow(self, flow_name: str, answers: list) -> dict:
        start = flow_runner.start_flow(WA, flow_name, tenant_id=TENANT)
        self.assertTrue(start.get("handled"), start)
        outcome = start
        if outcome.get("finished"):
            return outcome
        for answer in answers:
            outcome = flow_runner.handle_reply(WA, answer, tenant_id=TENANT)
            self.assertIsNotNone(outcome)
            if outcome.get("finished"):
                return outcome
        return outcome

    def test_book_callback_escalates_and_shows_hi_hint(self):
        outcome = self._finish_flow(
            "book_callback",
            [
                "Riya Shah",
                "9876543210",
                "skip",
                "Tomorrow 4 PM",
                "Need help with onboarding",
            ],
        )
        self.assertTrue(outcome.get("finished"), "callback flow should finish after details")
        text = self._outcome_text(outcome)
        self.assertIn("Type *hi*", text)
        self.assertEqual(database.get_user_state(WA).get("human_handover"), 1)

        with database.get_db_context() as conn:
            handoff = conn.execute(
                "SELECT reason, status FROM handoffs WHERE wa_id=?", (WA,)
            ).fetchone()
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff["reason"], "callback_request")

    def test_raise_ticket_escalates_and_shows_hi_hint(self):
        outcome = self._finish_flow(
            "raise_ticket",
            [
                "Riya Shah",
                "9876543210",
                "skip",
                "Payment failed twice yesterday",
                "urgent",
            ],
        )
        self.assertTrue(outcome.get("finished"))
        text = self._outcome_text(outcome)
        self.assertIn("Type *hi*", text)
        self.assertEqual(database.get_user_state(WA).get("human_handover"), 1)


class OrchestratorHiResumeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "handover.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        tenancy_store.ensure_tenant(
            TENANT, vertical="generic", display_name="Handover Test",
        )
        tenancy_cache.purge(TENANT)
        database.set_human_handover(WA, True)
        self.capture_tokens = whatsapp_sender.begin_capture(capture_only=True)

    def tearDown(self):
        whatsapp_sender.end_capture(self.capture_tokens)
        tenancy_cache.purge(TENANT)
        self.db_patch.stop()
        self.temp.cleanup()

    async def test_hi_clears_handover_and_is_not_ignored(self):
        from services.orchestrator import process_incoming_message

        result = await process_incoming_message(
            WA, "hi", capture_only=True, tenant_id=TENANT
        )
        self.assertEqual(database.get_user_state(WA).get("human_handover"), 0)
        self.assertNotEqual(result.get("status"), "ignored")
        self.assertFalse(result.get("human_handover"))

    async def test_random_message_stays_ignored_while_waiting(self):
        from services.orchestrator import process_incoming_message

        result = await process_incoming_message(
            WA, "any update on my ticket?", capture_only=True, tenant_id=TENANT
        )
        self.assertEqual(result.get("status"), "ignored")
        self.assertEqual(database.get_user_state(WA).get("human_handover"), 1)


if __name__ == "__main__":
    unittest.main()
