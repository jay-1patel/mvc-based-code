"""Regression tests for tenant-scoped WhatsApp intents and inbound dedup."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from backend.services import dedup
from backend.services import incoming_listener
from services import whatsapp_sender
from routing import message as message_router
from routing import webhook as webhook_router


class WhatsAppRoutingDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_careers_uses_explicit_tenant_profile(self):
        tenant_id = "leewaysoftech"
        expected = "Configured careers response"
        with (
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": tenant_id, "slug": "leeway-softech", "status": "active",
            }),
            patch.object(message_router, "_is_distributor", return_value=False),
            patch.object(message_router, "_tenant_feature_enabled", return_value=(True, tenant_id)),
            patch("backend.services.service_answers.answer_for_text", return_value=None),
            patch("backend.services.intent_answers.answer_for_text", return_value=expected) as answer,
            patch("faq.service.synthesize_intent_answer", new_callable=AsyncMock, return_value=expected),
            patch.object(message_router, "forward_to_bot", new_callable=AsyncMock) as fallback,
        ):
            result = await message_router.process_message(
                "💼 Career", "919512777899", tenant_id=tenant_id
            )

        self.assertEqual(result["answer"], expected)
        self.assertEqual(result["query_type"], "intent")
        # Registry slug wins over pack folder alias (leewaysoftech → leeway-softech).
        answer.assert_called_once_with(
            "919512777899", "💼 Career", tenant_id="leeway-softech"
        )
        fallback.assert_not_awaited()

    async def test_faq_forwarding_keeps_explicit_tenant(self):
        expected = {"answer": "tenant FAQ answer"}
        faq_service = ModuleType("backend.faq.service")
        faq_service.handle_faq_query = AsyncMock(return_value=expected)
        with (
            patch("shared.tenancy.gating.guard_result", return_value=None),
            patch.dict(sys.modules, {"backend.faq.service": faq_service}),
        ):
            result = await message_router.forward_to_bot(
                "faq", "question", "919512777899", tenant_id="tenant-a"
            )

        self.assertEqual(result, expected)
        faq_service.handle_faq_query.assert_awaited_once_with({
            "message": "question",
            "wa_id": "919512777899",
            "tenant_id": "tenant-a",
        })

    async def test_careers_button_uses_profile_answer_before_pipeline(self):
        expected = "Configured careers response"
        message = {
            "type": "interactive",
            "interactive": {
                "type": "list_reply",
                "list_reply": {"id": "menu_careers", "title": "Careers"},
            },
        }
        with (
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "leewaysoftech", "slug": "leeway-softech", "status": "active",
            }),
            patch.object(webhook_router, "_is_handover_active", return_value=False),
            patch.object(webhook_router, "_wants_human_agent", return_value=False),
            patch("backend.services.flow_runner.is_running", return_value=False),
            patch.object(webhook_router, "_is_doc_button_reply", return_value=False),
            patch("backend.services.menu_catalog.profile_button_status", return_value=(True, True)),
            patch("backend.services.flow_runner.flow_for_button", return_value=None),
            patch("backend.services.intent_answers.answer_for_button", return_value=expected) as answer,
            patch("faq.service.synthesize_intent_answer", new_callable=AsyncMock, return_value=expected),
            patch("routing.database.touch_customer"),
            patch.object(webhook_router, "log_incoming_message", new_callable=AsyncMock),
            patch.object(webhook_router, "log_outgoing_message", new_callable=AsyncMock),
            patch.object(webhook_router, "save_chat"),
            patch.object(webhook_router, "log_webhook"),
            patch.object(webhook_router.config, "SEND2_USERNAME", "test-user"),
            patch.object(webhook_router.config, "SEND2_PASSWORD", "test-password"),
            patch.object(webhook_router, "send_whatsapp_message") as send,
            patch.object(webhook_router, "process_message", new_callable=AsyncMock) as pipeline,
        ):
            await webhook_router._process_and_reply(
                "919512777899", "Test", "Careers", "interactive", message,
                "wamid.career", tenant_id="leewaysoftech",
            )

        answer.assert_called_once_with(
            "919512777899", "menu_careers", "leeway-softech"
        )
        send.assert_called_once_with("919512777899", expected)
        pipeline.assert_not_awaited()

    async def test_webhook_claims_repeated_meta_message_only_once(self):
        request = SimpleNamespace()
        request.body = AsyncMock(return_value=json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "contacts": [{"wa_id": "919512777899", "profile": {"name": "Test"}}],
                "messages": [{"id": "wamid.duplicate", "type": "text", "text": {"body": "hello"}}],
            }}]}],
        }).encode())
        tasks = SimpleNamespace(add_task=Mock())
        dedup._content_cache.clear()
        with (
            patch.object(webhook_router, "log_webhook"),
            patch("services.incoming_listener.read_poller_enabled", return_value=False),
            patch("shared.tenancy.resolver.resolve_tenant_from_payload", return_value="tenant-a"),
            patch.object(webhook_router, "_is_duplicate", side_effect=[False, True]) as claim,
            patch("backend.services.dedup.is_duplicate_content", return_value=False),
        ):
            first = await webhook_router.receive_webhook(request, tasks)
            second = await webhook_router.receive_webhook(request, tasks)

        self.assertEqual(first["type"], "accepted")
        self.assertEqual(second["type"], "duplicate")
        self.assertEqual(tasks.add_task.call_count, 1)
        self.assertEqual(claim.call_args_list[0].args, ("wamid.duplicate", "919512777899"))

    async def test_webhook_drops_message_when_phone_has_no_active_tenant(self):
        request = SimpleNamespace()
        request.body = AsyncMock(return_value=json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "unbound-business-number"},
                "contacts": [{"wa_id": "919500000000", "profile": {"name": "Test"}}],
                "messages": [{
                    "id": "wamid.unbound",
                    "type": "text",
                    "text": {"body": "hello"},
                }],
            }}]}],
        }).encode())
        tasks = SimpleNamespace(add_task=Mock())
        with (
            patch.object(webhook_router, "log_webhook"),
            patch("services.incoming_listener.read_poller_enabled", return_value=False),
            patch("shared.tenancy.resolver.resolve_tenant_from_payload", return_value=None),
            patch.object(webhook_router, "_is_duplicate", return_value=False),
            patch("backend.services.dedup.is_duplicate_content", return_value=False),
        ):
            result = await webhook_router.receive_webhook(request, tasks)

        self.assertEqual(result, {"status": "ignored", "type": "unbound_tenant"})
        tasks.add_task.assert_not_called()

    def test_database_dedup_blocks_repeats_across_local_caches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "dedup.sqlite3")
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                # A second initialization must remain safe and preserve the table.
                database.init_db()
                dedup._l1_cache.clear()

                self.assertFalse(dedup.is_duplicate("wamid.shared", "919512777899"))
                # Simulate a different worker with an empty process-local cache.
                dedup._l1_cache.clear()
                self.assertTrue(dedup.is_duplicate("wamid.shared", "919512777899"))
                self.assertFalse(dedup.is_duplicate("", "919512777899"))

        dedup._l1_cache.clear()

    def test_content_dedup_collapses_cross_channel_hi(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "dedup_content.sqlite3")
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                dedup._l1_cache.clear()
                dedup._content_cache.clear()
                self.assertFalse(dedup.is_duplicate_content("919512777899", "hi"))
                self.assertTrue(dedup.is_duplicate_content("919512777899", "Hi"))
                self.assertTrue(dedup.try_claim_message("wamid.poll-hi", "919512777899"))
                dedup._l1_cache.clear()
                self.assertFalse(dedup.try_claim_message("wamid.poll-hi", "919512777899"))
        dedup._l1_cache.clear()
        dedup._content_cache.clear()

    async def test_greeting_short_circuits_before_intent(self):
        menu = {
            "type": "list",
            "header": "Leeway",
            "body": "Welcome",
            "button_text": "Menu",
            "sections": [{"title": "Options", "rows": [{"id": "a", "title": "A"}]}],
        }
        with (
            patch.object(message_router, "_is_distributor", return_value=False),
            patch(
                "shared.tenancy.resolver.resolve_effective_tenant_id",
                return_value="leeway-softech",
            ),
            patch.object(message_router, "_get_greeting_menu", return_value=menu) as greet,
            patch("backend.services.intent_answers.answer_for_text") as intent,
            patch.object(message_router, "forward_to_bot", new_callable=AsyncMock) as fallback,
        ):
            result = await message_router.process_message("hi", "919512777899", tenant_id="leeway")

        self.assertEqual(result["interactive"], menu)
        self.assertEqual(result["query_type"], "greeting")
        greet.assert_called_once_with("919512777899", tenant_id="leeway-softech")
        intent.assert_not_called()
        fallback.assert_not_awaited()

    async def test_message_without_active_tenant_stops_before_classification(self):
        with (
            patch(
                "shared.tenancy.resolver.resolve_effective_tenant_id",
                return_value=None,
            ),
            patch.object(message_router, "classify_query") as classify,
        ):
            result = await message_router.process_message("hello", "919500000000")

        self.assertEqual(result["query_type"], "ignored")
        self.assertEqual(result["answer"], "")
        classify.assert_not_called()

    async def test_tenant_resolution_exception_stops_message_processing(self):
        with (
            patch(
                "shared.tenancy.resolver.resolve_effective_tenant_id",
                side_effect=RuntimeError("registry unavailable"),
            ),
            patch.object(message_router, "classify_query") as classify,
        ):
            result = await message_router.process_message("hello", "919500000000")

        self.assertEqual(result["query_type"], "error")
        self.assertIn("Tenant resolution failed", result["error"])
        classify.assert_not_called()

    async def test_orchestrator_ignores_message_without_active_tenant(self):
        from backend.services import orchestrator

        # The ignore decision happens after a user_states read, so the test
        # needs its own database. No tenant is assigned, and the message
        # must not be processed.
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "orch.sqlite3")
            with (
                patch.object(database, "DB_PATH", db_path),
                patch(
                    "shared.tenancy.resolver.resolve_effective_tenant_id",
                    return_value=None,
                ),
            ):
                database.init_db()
                result = await orchestrator.process_incoming_message(
                    "919500000000", "hello", capture_only=True
                )

        self.assertEqual(result["status"], "ignored")
        self.assertEqual(result["messages"], [])

    def test_tenant_lookup_rejects_prefix_false_positives(self):
        from shared.tenancy import store as tenancy_store

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "tenants.sqlite3")
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                tenancy_store.ensure_tenant(
                    "leeway-softech", slug="leeway-softech",
                    vertical="it_software", display_name="Leeway Softech",
                )
                tenancy_store.ensure_tenant(
                    "ltfinance", slug="ltfinance",
                    vertical="finance", display_name="L&T Finance",
                )
                # Bare "l" must NOT resolve to leeway-softech / ltfinance
                self.assertIsNone(tenancy_store.get_tenant("l"))
                self.assertEqual(
                    tenancy_store.get_tenant("leewaysoftech")["slug"], "leeway-softech"
                )
                self.assertEqual(
                    tenancy_store.get_tenant("leeway-softech")["slug"], "leeway-softech"
                )

    async def test_listener_processes_each_polled_message_id_once(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: [
                {"messageid": "wamid.poll", "Number": "919512777899", "Message": "Career"},
                {"messageid": "wamid.poll", "Number": "919512777899", "Message": "Career"},
            ],
        )
        client = SimpleNamespace(post=AsyncMock(return_value=response))
        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return client

            async def __aexit__(self, *_args):
                return False
        processed = {"messages": [{"type": "text", "text": "answer"}]}
        stop_event = asyncio.Event()

        async def sleep(_seconds):
            stop_event.set()

        with (
            patch.object(incoming_listener, "SEND2_POLLER_ENABLED", True),
            patch.object(incoming_listener, "SEND2_INCOMING_URL", "https://example.test/incoming"),
            patch.object(incoming_listener, "SEND2_USERNAME", "username"),
            patch.object(incoming_listener, "SEND2_PASSWORD", "password"),
            patch.object(incoming_listener, "SEND2_NUMBER", "15550001111"),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="tenant-a"),
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "tenant-a", "slug": "tenant-a", "status": "active",
            }),
            patch.object(incoming_listener.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(incoming_listener, "POLL_INTERVAL_SECONDS", 0),
            patch.object(incoming_listener, "_was_processed", side_effect=[False, True]) as claim,
            patch.object(incoming_listener, "_claim_inbound", return_value=True),
            patch.object(incoming_listener, "_is_duplicate_content", return_value=False),
            patch.object(incoming_listener, "_record_processed") as record,
            patch("services.orchestrator.process_incoming_message", new_callable=AsyncMock, return_value=processed) as process,
            patch.object(incoming_listener, "_send_reply", new_callable=AsyncMock) as send_reply,
            patch.object(incoming_listener.asyncio, "sleep", side_effect=sleep),
        ):
            await incoming_listener.poll_incoming_messages(stop_event)

        self.assertEqual(claim.call_count, 2)
        process.assert_awaited_once_with(
            "919512777899", "Career", capture_only=True, tenant_id="tenant-a"
        )
        send_reply.assert_awaited_once_with(
            "919512777899", processed["messages"], tenant_id="tenant-a"
        )

    async def test_orchestrator_capture_only_prevents_listener_double_send(self):
        from services import orchestrator

        class FakeDb:
            @staticmethod
            def execute(query, params=()):
                if query.startswith("SELECT state"):
                    return SimpleNamespace(fetchone=lambda: None)
                if query.startswith("SELECT 1 FROM distributors"):
                    return SimpleNamespace(fetchone=lambda: (1,))
                return SimpleNamespace(fetchone=lambda: None)

        @contextmanager
        def fake_db_context():
            yield FakeDb()

        async def b2b_reply(*_args):
            await whatsapp_sender.send_text_message("919512777899", "one reply")
            return {"status": "processed"}

        with (
            patch.object(orchestrator, "get_db_context", fake_db_context),
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "tenant-a", "slug": "tenant-a", "status": "active",
            }),
            patch("bots.distributor_b2b.workflows.handle_b2b_message", new=b2b_reply),
            patch("kb.services.whatsapp.send_whatsapp_message") as live_send,
        ):
            result = await orchestrator.process_incoming_message(
                "919512777899", "distributor order inquiry", capture_only=True,
                tenant_id="tenant-a",
            )

        self.assertEqual(result["messages"], [{"type": "text", "text": "one reply", "sent_live": False}])
        live_send.assert_not_called()

    async def test_capture_only_records_without_live_send(self):
        tokens = whatsapp_sender.begin_capture(capture_only=True)
        try:
            with patch(
                "kb.services.whatsapp.send_whatsapp_message"
            ) as live_send:
                await whatsapp_sender.send_text_message("919512777899", "one reply")
                live_send.assert_not_called()
                self.assertEqual(
                    whatsapp_sender.drain_captured(),
                    [{"type": "text", "text": "one reply", "sent_live": False}],
                )
        finally:
            whatsapp_sender.end_capture(tokens)


if __name__ == "__main__":
    unittest.main()
