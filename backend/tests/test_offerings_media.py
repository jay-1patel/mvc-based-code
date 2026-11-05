"""Offerings browse must recognize Explore Offerings and send CTA image cards."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services import offerings_flow  # noqa: E402


class OfferingsRequestTests(unittest.TestCase):
    def test_explore_offerings_title_matches_tours(self):
        with patch.object(offerings_flow, "_offerings_button_keys", return_value=set()):
            self.assertTrue(
                offerings_flow.is_offerings_request(
                    "makemytrip-india-private-limited", "Explore Offerings"
                )
            )
            self.assertTrue(
                offerings_flow.is_offerings_request(
                    "makemytrip-india-private-limited",
                    "ignored",
                    pressed_id="browse_offerings",
                )
            )

    def test_profile_menu_title_match(self):
        keys = {"browse_offerings", "explore offerings", "Explore Offerings"}
        with patch.object(offerings_flow, "_offerings_button_keys", return_value=keys):
            self.assertTrue(
                offerings_flow.is_offerings_request(
                    "tid", "Explore Offerings"
                )
            )

    def test_all_vertical_browse_titles_match(self):
        titles = [
            "Explore Properties",
            "View Products",
            "Our Services",
            "Banking Products",
            "Packages",
            "What We Offer",
        ]
        with patch.object(offerings_flow, "_offerings_button_keys", return_value=set()):
            for title in titles:
                self.assertTrue(
                    offerings_flow.is_offerings_request("tid", title),
                    msg=f"should match browse title {title!r}",
                )

    def test_foreign_browse_title_ignored_when_profile_has_row(self):
        # Ecommerce Leeway menu says \"Our Offerings\"; an old WhatsApp list
        # still showing \"Our Services\" must NOT open the catalogue.
        keys = {"browse_offerings", "our offerings", "Our Offerings"}
        with patch.object(offerings_flow, "_offerings_button_keys", return_value=keys):
            self.assertFalse(
                offerings_flow.is_offerings_request("tid", "Our Services")
            )
            self.assertFalse(
                offerings_flow.is_offerings_request("tid", "🛠️ Our Services")
            )
            self.assertTrue(
                offerings_flow.is_offerings_request("tid", "Our Offerings")
            )

    def test_brochure_button_not_treated_as_browse(self):
        btn = type("B", (), {
            "enabled": True, "id": "menu_brochure", "title": "Brochure",
            "intent": "brochure_request", "requires_feature": "brochure_pdf",
        })()
        self.assertFalse(offerings_flow._is_offerings_menu_button(btn))

    def test_main_products_feature_is_browse(self):
        btn = type("B", (), {
            "enabled": True, "id": "custom_shop", "title": "Shop",
            "intent": "", "requires_feature": "products",
        })()
        self.assertTrue(offerings_flow._is_offerings_menu_button(btn))


class SendProductDetailsMediaTests(unittest.TestCase):
    def test_sends_cta_url_with_image(self):
        product = {
            "id": 42,
            "name": "Kashmir Paradise",
            "media_url": "https://i.img402.dev/pd2qhqgfk0.png",
            "media_type": "image",
            "short_description": "4N/5D Srinagar",
            "attrs_json": {"website": "https://www.makemytrip.com/kashmir"},
        }
        sender = SimpleNamespace(
            send_cta_url_message=AsyncMock(return_value=True),
            send_document_message=AsyncMock(return_value=True),
            send_text_message=AsyncMock(return_value=True),
            send_image_message=AsyncMock(return_value=True),
        )
        with patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "remember_product"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"):
            ok = asyncio.run(
                offerings_flow.send_product_details("919512777899", product, "tid")
            )
        self.assertTrue(ok)
        sender.send_cta_url_message.assert_awaited_once()
        kwargs = sender.send_cta_url_message.await_args.kwargs
        self.assertEqual(kwargs["image_url"], "https://i.img402.dev/pd2qhqgfk0.png")
        self.assertTrue(kwargs["cta_url"].startswith("https://"))
        sender.send_document_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
