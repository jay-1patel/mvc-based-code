"""WhatsApp-replica test console: parity, delivery rendering, D-16, safety.

The replica promise: a message typed into the admin panel's WhatsApp Tester
produces EXACTLY the reply payloads the real WhatsApp path produces, and the
panel renders the delivery-shaped form of those payloads (what the phone
shows). These tests pin all three halves of that promise:

  * Parity — the simulator endpoint and the production entry call
    (``services.incoming_listener`` invokes
    ``process_incoming_message(..., capture_only=True, tenant_id=...)``)
    return identical captured payloads for the same input script, and the
    endpoint's rendered ``whatsapp`` view equals rendering that capture.
  * Rendering — ``services.whatsapp_render.render_phone_messages`` applies
    the production delivery layer's shaping (formatting normalization,
    WhatsApp interactive limits, body splits, fallbacks).
  * D-16 — ``POST /api/test-simulator/menu-preview`` no longer 500s (it
    imported menu builders that do not exist) and previews the real menus.
  * Safety — a simulator run performs ZERO live sends: every live sender is
    rigged to explode, and every captured payload must say sent_live=False.
"""

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
from services.whatsapp_render import render_phone_messages
from shared.tenancy import cache as tenancy_cache
from shared.tenancy import store as tenancy_store

TENANT = "replica-test-tenant"
SUPER_ADMIN = {"role": "super_admin", "id": 1, "username": "root"}


# ---------------------------------------------------------------------------
# Renderer unit tests (pure — no DB)
# ---------------------------------------------------------------------------


class RenderTextTests(unittest.TestCase):
    def test_markdown_is_normalized_to_whatsapp_formatting(self):
        out = render_phone_messages(
            [{"type": "text", "text": "Hello **there**\n\n| Name | Price |\n| --- | --- |\n| Shoes | 499 |"}]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "text")
        self.assertIn("*there*", out[0]["text"])
        self.assertNotIn("**", out[0]["text"])
        # Markdown tables become WhatsApp bullet lines in production sends.
        self.assertIn("• ", out[0]["text"])

    def test_long_text_is_chunked_like_production(self):
        text = " ".join(["word"] * 1500)  # ~7500 chars
        out = render_phone_messages([{"type": "text", "text": text}])
        self.assertGreaterEqual(len(out), 2)
        for msg in out:
            self.assertEqual(msg["kind"], "text")
            self.assertLessEqual(len(msg["text"]), 4000)
            self.assertEqual(msg["source_index"], 0)

    def test_empty_text_renders_nothing(self):
        self.assertEqual(render_phone_messages([{"type": "text", "text": ""}]), [])
        self.assertEqual(render_phone_messages([]), [])


class RenderButtonsTests(unittest.TestCase):
    def test_buttons_render_as_quick_reply_buttons(self):
        out = render_phone_messages(
            [
                {
                    "type": "buttons",
                    "text": "Pick one",
                    "buttons": [
                        {"type": "reply", "reply": {"id": "a", "title": "Alpha"}},
                        {"type": "reply", "reply": {"id": "b", "title": "Beta"}},
                    ],
                }
            ]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "buttons")
        self.assertEqual(out[0]["text"], "Pick one")
        self.assertEqual(
            out[0]["buttons"],
            [{"id": "a", "title": "Alpha"}, {"id": "b", "title": "Beta"}],
        )

    def test_flat_buttons_are_normalized_and_capped(self):
        buttons = [{"id": f"b{i}", "title": "T" * 30} for i in range(5)]
        out = render_phone_messages([{"type": "buttons", "text": "Pick", "buttons": buttons}])
        self.assertEqual(len(out), 1)
        # WhatsApp allows at most 3 reply buttons, titles <= 20 chars.
        self.assertEqual(len(out[0]["buttons"]), 3)
        self.assertTrue(all(len(b["title"]) <= 20 for b in out[0]["buttons"]))

    def test_buttons_without_valid_buttons_fall_back_to_text(self):
        out = render_phone_messages(
            [{"type": "buttons", "text": "Just words", "buttons": [{"title": "no id"}]}]
        )
        self.assertEqual([m["kind"] for m in out], ["text"])
        self.assertEqual(out[0]["text"], "Just words")

    def test_long_button_body_splits_into_text_plus_prompt(self):
        body = " ".join(["detail"] * 200)  # > 950 chars once formatted
        out = render_phone_messages(
            [
                {
                    "type": "buttons",
                    "text": body,
                    "buttons": [{"id": "x", "title": "Go"}],
                }
            ]
        )
        self.assertEqual(out[0]["kind"], "text")
        self.assertIn("detail", out[0]["text"])
        self.assertEqual(out[-1]["kind"], "buttons")
        self.assertEqual(out[-1]["text"], "Please select an option below:")

    def test_header_media_is_delivered_before_buttons(self):
        out = render_phone_messages(
            [
                {
                    "type": "buttons",
                    "text": "Catalogue",
                    "buttons": [{"id": "main_menu", "title": "Main Menu"}],
                    "header_media": {
                        "type": "document",
                        "document": {"link": "https://files.example.com/cat.pdf", "filename": "cat.pdf"},
                    },
                }
            ]
        )
        self.assertEqual(out[0]["kind"], "document")
        self.assertEqual(out[0]["media_url"], "https://files.example.com/cat.pdf")
        self.assertEqual(out[1]["kind"], "buttons")


class RenderListTests(unittest.TestCase):
    def test_list_shaping_enforces_whatsapp_limits(self):
        out = render_phone_messages(
            [
                {
                    "type": "list",
                    "header": "H" * 80,
                    "body": "Choose",
                    "button_text": "B" * 30,
                    "sections": [
                        {
                            "title": "S" * 40,
                            "rows": [
                                {"id": "r1", "title": "T" * 40, "description": "D" * 100},
                                {"id": "r1", "title": "Dup", "description": ""},
                                {"id": "", "title": "No id", "description": ""},
                            ],
                        }
                    ],
                }
            ]
        )
        self.assertEqual(len(out), 1)
        msg = out[0]
        self.assertEqual(msg["kind"], "list")
        self.assertLessEqual(len(msg["header"]), 60)
        self.assertLessEqual(len(msg["button_text"]), 20)
        section = msg["sections"][0]
        self.assertLessEqual(len(section["title"]), 24)
        rows = section["rows"]
        # Empty-id row dropped; duplicate id suffixed, as send2 requires.
        self.assertEqual([r["id"] for r in rows], ["r1", "r1_2"])
        self.assertLessEqual(len(rows[0]["title"]), 24)
        self.assertLessEqual(len(rows[0]["description"]), 72)

    def test_long_list_body_splits_into_text_plus_prompt(self):
        body = " ".join(["info"] * 300)
        out = render_phone_messages(
            [
                {
                    "type": "list",
                    "header": "Menu",
                    "body": body,
                    "button_text": "Show Options",
                    "sections": [{"title": "Options", "rows": [{"id": "a", "title": "A"}]}],
                }
            ]
        )
        self.assertEqual(out[0]["kind"], "text")
        self.assertEqual(out[-1]["kind"], "list")
        self.assertEqual(out[-1]["body"], "Please select an option below:")

    def test_list_without_sections_or_body_renders_nothing(self):
        self.assertEqual(
            render_phone_messages(
                [{"type": "list", "header": "H", "body": "B", "sections": []}]
            ),
            [],
        )
        self.assertEqual(
            render_phone_messages(
                [{"type": "list", "header": "H", "body": "", "sections": [{"title": "S", "rows": []}]}]
            ),
            [],
        )


class RenderOtherKindsTests(unittest.TestCase):
    def test_cta_url_rendering(self):
        out = render_phone_messages(
            [
                {
                    "type": "cta_url",
                    "text": "See the collection",
                    "cta_url": "https://shop.example.com",
                    "display_text": "Visit the shop right now",
                    "image_url": "https://cdn.example.com/banner.jpg",
                    "footer_text": "Tap to open",
                }
            ]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "cta_url")
        self.assertEqual(out[0]["url"], "https://shop.example.com")
        self.assertLessEqual(len(out[0]["display_text"]), 20)
        self.assertEqual(out[0]["image_url"], "https://cdn.example.com/banner.jpg")

    def test_cta_without_url_falls_back_to_text(self):
        out = render_phone_messages(
            [{"type": "cta_url", "text": "Words only", "cta_url": "", "image_url": ""}]
        )
        self.assertEqual([m["kind"] for m in out], ["text"])

    def test_image_caption_becomes_following_text(self):
        out = render_phone_messages(
            [
                {
                    "type": "image",
                    "media_url": "https://cdn.example.com/p.jpg",
                    "caption": "Red shoes",
                }
            ]
        )
        self.assertEqual([m["kind"] for m in out], ["image", "text"])
        self.assertEqual(out[1]["text"], "Red shoes")

    def test_document_rendering(self):
        out = render_phone_messages(
            [{"type": "document", "media_url": "https://f.example.com/a.pdf", "filename": "a.pdf"}]
        )
        self.assertEqual(out[0]["kind"], "document")
        self.assertEqual(out[0]["filename"], "a.pdf")

    def test_unknown_type_is_skipped(self):
        self.assertEqual(render_phone_messages([{"type": "sticker", "text": "x"}]), [])


class MenuSmartSwitchRenderTests(unittest.TestCase):
    """≤3 menu options render as quick-reply buttons; >3 as a list menu.

    This is the Smart Switch in bots.customer_b2c.menus.get_main_menu plus
    the renderer's projection of its payloads — the two renderings a
    customer can ever see for a main menu.
    """

    def _menu_payload(self, n_buttons: int) -> dict:
        from shared.tenancy.schemas import TenantProfile

        buttons = [
            {"id": f"menu_item_{i}", "title": f"Option {i}"} for i in range(n_buttons)
        ]
        profile = TenantProfile(
            tenant_id="switch-test",
            vertical="ecommerce",
            display_name="Switch Store",
            brand={"name": "Switch Store"},
            menu={"buttons": buttons},
        )
        with patch(
            "shared.tenancy.resolver.resolve_effective_tenant_id",
            return_value="switch-test",
        ), patch("shared.tenancy.loader.get_tenant_profile", return_value=profile):
            from bots.customer_b2c import menus

            return menus.get_main_menu("919999999992", tenant_id="switch-test")

    def test_two_options_render_as_buttons(self):
        payload = self._menu_payload(2)
        self.assertEqual(payload["type"], "buttons")
        out = render_phone_messages([payload])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "buttons")
        self.assertEqual(len(out[0]["buttons"]), 2)

    def test_five_options_render_as_list(self):
        payload = self._menu_payload(5)
        self.assertEqual(payload["type"], "list")
        out = render_phone_messages([payload])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "list")
        rows = [r for s in out[0]["sections"] for r in s["rows"]]
        self.assertEqual(len(rows), 5)


# ---------------------------------------------------------------------------
# Endpoint tests: parity, D-16, zero live sends (temp DB, like the
# handover-resume suite)
# ---------------------------------------------------------------------------


class SimulatorEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "replica.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        tenancy_store.ensure_tenant(TENANT, vertical="ecommerce", display_name="Replica Store")
        tenancy_cache.purge(TENANT)

    def tearDown(self):
        tenancy_cache.purge(TENANT)
        self.db_patch.stop()
        self.temp.cleanup()

    async def _webhook_path(self, wa_id: str, message: str) -> dict:
        """The production entry: exactly the call incoming_listener makes."""
        from services.orchestrator import process_incoming_message

        return await process_incoming_message(
            wa_id, message, capture_only=True, tenant_id=TENANT
        )

    async def _replica_path(self, wa_id: str, message: str) -> dict:
        from routes.test_simulator import SimulatorRequest, simulate_message

        return await simulate_message(
            SimulatorRequest(wa_id=wa_id, message=message, user_type="b2c", tenant_id=TENANT),
            current_admin=SUPER_ADMIN,
        )

    async def test_parity_same_inputs_identical_payloads(self):
        script = ["Hi", "menu_products", "Hi"]
        for step, message in enumerate(script):
            hook = await self._webhook_path("919999999993", message)
            sim = await self._replica_path("919999999994", message)
            self.assertEqual(
                sim.get("messages"),
                hook.get("messages"),
                f"step {step} ({message!r}): replica payloads differ from webhook path",
            )
            self.assertEqual(sim.get("status"), hook.get("status"))
            # The rendered view must be the rendering of that same capture.
            self.assertEqual(
                sim.get("whatsapp"),
                render_phone_messages(hook.get("messages") or []),
            )
            self.assertTrue(sim.get("whatsapp"), f"step {step}: empty rendered view")

    async def test_zero_live_sends(self):
        live_senders = [
            "backend.app.services.whatsapp.routing_sender.send_whatsapp_message",
            "backend.app.services.whatsapp.routing_sender.send_whatsapp_list_menu",
            "backend.app.services.whatsapp.routing_sender.send_whatsapp_cta_url",
            "backend.app.services.whatsapp.kb_sender.send_whatsapp_message",
            "backend.app.services.whatsapp.kb_sender.send_interactive_buttons",
            "backend.app.services.whatsapp.kb_sender.send_document",
        ]
        patches = [
            patch(name, side_effect=AssertionError(f"live send attempted: {name}"))
            for name in live_senders
        ]
        for p in patches:
            p.start()
        try:
            result = await self._replica_path("919999999995", "Hi")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(result.get("status"), "processed")
        self.assertTrue(result.get("messages"))
        for msg in result["messages"]:
            self.assertFalse(
                msg.get("sent_live"),
                f"captured message claims a live send: {msg}",
            )

    async def test_menu_preview_b2c_no_longer_500s(self):
        from routes.test_simulator import MenuPreviewRequest, preview_menu

        result = await preview_menu(
            MenuPreviewRequest(wa_id="919999999996", user_type="b2c", tenant_id=TENANT),
            current_admin=SUPER_ADMIN,
        )
        self.assertIn(result["menu"].get("type"), ("buttons", "list"))
        self.assertTrue(result["meta"]["ids"], "preview menu has no option ids")
        self.assertTrue(result["whatsapp"], "preview has no rendered WhatsApp view")
        self.assertEqual(result["whatsapp"][0]["kind"], result["menu"]["type"])

    async def test_menu_preview_b2b_no_longer_500s(self):
        from routes.test_simulator import MenuPreviewRequest, preview_menu

        result = await preview_menu(
            MenuPreviewRequest(wa_id="919999999997", user_type="b2b", tenant_id=TENANT),
            current_admin=SUPER_ADMIN,
        )
        self.assertEqual(result["menu"].get("type"), "list")
        self.assertTrue(result["meta"]["ids"])
        self.assertTrue(result["whatsapp"])


if __name__ == "__main__":
    unittest.main()
