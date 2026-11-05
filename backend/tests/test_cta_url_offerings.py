"""Interactive cta_url offering cards (image header + description + URL)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from services import offerings_flow  # noqa: E402
import routing.whatsapp as wa  # noqa: E402


class CtaUrlPayloadTests(unittest.TestCase):
    def test_payload_shape_matches_send2_spec(self):
        captured = {}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, **kwargs):
                captured["url"] = url
                captured["payload"] = json

                class Resp:
                    status_code = 200
                    text = "Your Message ID : ABC , Message Sent Successfully"

                return Resp()

        with patch.object(wa, "get_tenant_send2_credentials", return_value=("u", "p")), \
             patch.object(wa.httpx, "Client", FakeClient):
            ok = wa.send_whatsapp_cta_url(
                "919512777899",
                body_text="Kashmir Paradise\n\n4N/5D Srinagar",
                cta_url="https://www.makemytrip.com",
                display_text="View Package",
                image_url="https://i.img402.dev/demo.png",
                footer_text="MakeMyTrip",
            )
        self.assertTrue(ok)
        p = captured["payload"]
        self.assertEqual(p["message_type"], "interactive")
        inter = p["interactive"]
        self.assertEqual(inter["type"], "cta_url")
        self.assertEqual(inter["header"]["type"], "image")
        self.assertEqual(inter["header"]["image"]["link"], "https://i.img402.dev/demo.png")
        self.assertIn("Kashmir", inter["body"]["text"])
        self.assertEqual(inter["action"]["name"], "cta_url")
        self.assertEqual(inter["action"]["parameters"]["url"], "https://www.makemytrip.com")
        self.assertEqual(inter["action"]["parameters"]["display_text"], "View Package")
        # Must not mix reply buttons into CTA payload
        self.assertNotIn("buttons", inter.get("action", {}))


class OfferingCtaCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_cta_with_image_not_brochure(self):
        product = {
            "id": 42,
            "name": "Kashmir Paradise",
            "media_url": "https://i.img402.dev/k.png",
            "media_type": "image",
            "short_description": "4N Srinagar lakeside",
            "attrs_json": {
                "destination": "Srinagar",
                "duration": "4N/5D",
                "website": "https://www.makemytrip.com/kashmir",
                "brochure_url": "https://example.com/uploaded_brochure.pdf",
            },
        }
        sender = AsyncMock()
        sender.send_cta_url_message.return_value = True
        sender.send_document_message.return_value = True
        sender.send_text_message.return_value = True
        with patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"), \
             patch.object(offerings_flow, "remember_product"):
            ok = await offerings_flow.send_offering_cta_card("wa", product, "tid")
        self.assertTrue(ok)
        sender.send_cta_url_message.assert_awaited_once()
        kwargs = sender.send_cta_url_message.await_args.kwargs
        self.assertEqual(kwargs["image_url"], "https://i.img402.dev/k.png")
        self.assertEqual(kwargs["cta_url"], "https://www.makemytrip.com/kashmir")
        self.assertEqual(kwargs["display_text"], "View Package")
        sender.send_document_message.assert_not_awaited()

    async def test_pdf_only_media_does_not_auto_send_document(self):
        """Product whose only media is the extracted brochure PDF must not
        push that PDF on Explore — brochure is ask-only."""
        product = {
            "id": 7,
            "name": "Goa Escape",
            "media_url": "https://example.com/brochure.pdf",
            "media_type": "document",
            "short_description": "Beach holiday",
            "attrs_json": {
                "brochure_url": "https://example.com/brochure.pdf",
                "website": "https://example.com/goa",
            },
        }
        sender = AsyncMock()
        sender.send_cta_url_message.return_value = True
        sender.send_document_message.return_value = True
        with patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"), \
             patch.object(offerings_flow, "remember_product"):
            ok = await offerings_flow.send_offering_cta_card("wa", product, "tid")
        self.assertTrue(ok)
        sender.send_document_message.assert_not_awaited()
        kwargs = sender.send_cta_url_message.await_args.kwargs
        self.assertFalse(kwargs.get("image_url"))

    async def test_explore_sends_list_not_cta_blast(self):
        products = [
            {
                "id": i,
                "name": f"Pkg {i}",
                "media_url": f"https://i.img402.dev/{i}.png",
                "media_type": "image",
                "short_description": "Trip",
                "attrs_json": {"website": "https://example.com"},
                "is_active": 1,
            }
            for i in range(1, 4)
        ]

        class FakeDb:
            def list_products(self, **kwargs):
                return list(products)

        sender = AsyncMock()
        sender.send_list_menu.return_value = True
        with patch.object(offerings_flow, "_db", lambda: FakeDb()), \
             patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"), \
             patch.object(offerings_flow, "remember_offerings_page"), \
             patch.object(
                 offerings_flow, "send_offering_cta_card", new_callable=AsyncMock, return_value=True,
             ) as card:
            ok = await offerings_flow.send_offerings_list("wa", tenant_id="t")
        self.assertTrue(ok)
        sender.send_list_menu.assert_awaited_once()
        card.assert_not_awaited()

    async def test_product_tap_sends_cta_card(self):
        product = {
            "id": 42,
            "name": "Kashmir Paradise",
            "media_url": "https://i.img402.dev/k.png",
            "media_type": "image",
            "short_description": "Lakeside",
            "attrs_json": {"website": "https://www.makemytrip.com/kashmir"},
        }

        class FakeDb:
            def get_product(self, pid, tenant_id=None):
                return product if pid == 42 else None

            def list_products(self, **kwargs):
                return [product]

        sender = AsyncMock()
        sender.send_cta_url_message.return_value = True
        with patch.object(offerings_flow, "_db", lambda: FakeDb()), \
             patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"), \
             patch.object(offerings_flow, "remember_product"), \
             patch.object(offerings_flow, "is_offerings_request", return_value=False), \
             patch.object(offerings_flow, "find_product", return_value=None):
            result = await offerings_flow.handle_offerings_action(
                "wa", "prod_42", tenant_id="t",
            )
        self.assertTrue(result and result.get("handled"))
        sender.send_cta_url_message.assert_awaited_once()


class OfferingCtaUrlResolveTests(unittest.TestCase):
    def test_prefers_product_website(self):
        p = {"attrs_json": {"website": "https://a.example/x"}}
        with patch.object(offerings_flow, "_tenant_website", return_value="https://brand.example"):
            self.assertEqual(offerings_flow.offering_cta_url(p, "t"), "https://a.example/x")

    def test_falls_back_to_brand_site(self):
        with patch.object(offerings_flow, "_tenant_website", return_value="https://brand.example"):
            self.assertEqual(offerings_flow.offering_cta_url({}, "t"), "https://brand.example")


class BrochureAskTests(unittest.TestCase):
    def test_brochure_phrases_match_pdf_ask_not_explore(self):
        from services.brochure_service import is_brochure_ask

        self.assertTrue(is_brochure_ask("brochure"))
        self.assertTrue(is_brochure_ask("send me the pdf"))
        self.assertTrue(is_brochure_ask("menu_brochure"))
        self.assertTrue(is_brochure_ask("Brochure", button_id="menu_brochure"))
        self.assertFalse(is_brochure_ask("Explore Offerings"))
        self.assertFalse(is_brochure_ask("browse_offerings"))


if __name__ == "__main__":
    unittest.main()
