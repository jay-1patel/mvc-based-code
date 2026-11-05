"""Offerings browse helpers still used by CTA card flow."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from services import offerings_flow  # noqa: E402


def _product(pid, name, category="General", **extra):
    row = {
        "id": pid,
        "name": name,
        "category": category,
        "price": "",
        "short_description": extra.pop("short_description", ""),
        "description": "",
        "media_url": extra.pop("media_url", ""),
        "media_type": extra.pop("media_type", "image"),
        "attrs_json": extra.pop("attrs_json", {}),
        "is_active": 1,
        "tenant_id": "t",
    }
    row.update(extra)
    return row


class FakeDb:
    def __init__(self, products):
        self._products = list(products)

    def list_products(self, category=None, active_only=True, limit=100, tenant_id=None):
        rows = self._products
        if category:
            wanted = (category or "").strip().casefold()
            rows = [p for p in rows if (p.get("category") or "").strip().casefold() == wanted]
        return list(rows)[:limit]


class PageSliceTests(unittest.TestCase):
    def test_eleven_items_page0_has_more(self):
        products = [_product(i, f"P{i}") for i in range(1, 12)]
        page_rows, has_more, next_page = offerings_flow._page_slice(products, 0)
        self.assertEqual(len(page_rows), 9)
        self.assertTrue(has_more)
        self.assertEqual(next_page, 1)


class RowDescriptionTests(unittest.TestCase):
    def test_tours_prefers_destination_duration(self):
        p = _product(
            1, "Kashmir",
            attrs_json={"destination": "Srinagar", "duration": "4N/5D"},
            short_description="fallback text",
        )
        self.assertEqual(
            offerings_flow._row_description(p, "tours_travel"),
            "Srinagar • 4N/5D",
        )


class SendOfferingsListTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_catalogue_sends_text(self):
        sender = AsyncMock()
        sender.send_text_message.return_value = True
        with patch.object(offerings_flow, "_db", lambda: FakeDb([])), \
             patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"):
            ok = await offerings_flow.send_offerings_list("wa", tenant_id="t")
        self.assertTrue(ok)
        sender.send_text_message.assert_awaited()

    async def test_explore_sends_list_sorted_by_id(self):
        products = [
            _product(3, "C", attrs_json={"website": "https://ex.com"}),
            _product(1, "A", attrs_json={"website": "https://ex.com"}),
            _product(2, "B", attrs_json={"website": "https://ex.com"}),
        ]
        sender = AsyncMock()
        sender.send_list_menu.return_value = True

        with patch.object(offerings_flow, "_db", lambda: FakeDb(products)), \
             patch.object(offerings_flow, "_sender", return_value=sender), \
             patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_brand_name", return_value="MMT"), \
             patch.object(offerings_flow, "remember_offerings_page"):
            ok = await offerings_flow.send_offerings_list("wa", tenant_id="t")
        self.assertTrue(ok)
        sender.send_list_menu.assert_awaited_once()
        sections = sender.send_list_menu.await_args.kwargs.get("sections") or \
            sender.send_list_menu.await_args.args[4]
        titles = [r["title"] for r in sections[0]["rows"]]
        self.assertEqual(titles, ["A", "B", "C"])
        self.assertTrue(all(r["id"].startswith("prod_") for r in sections[0]["rows"]))

    async def test_list_more_row_when_over_ten(self):
        products = [_product(i, f"P{i}") for i in range(1, 12)]
        sections, page, has_more = offerings_flow.build_offerings_list_sections(
            products, "tours_travel", page=0,
        )
        self.assertTrue(has_more)
        self.assertEqual(page, 0)
        rows = sections[0]["rows"]
        self.assertEqual(len(rows), 10)  # 9 products + More
        self.assertTrue(rows[-1]["id"].startswith("offerings_more_"))


class ExploreRequestTests(unittest.TestCase):
    def test_explore_offerings_matched(self):
        with patch.object(offerings_flow, "get_tenant_vertical", return_value="tours_travel"), \
             patch.object(offerings_flow, "_offerings_button_keys", return_value=set()):
            self.assertTrue(
                offerings_flow.is_offerings_request("t", "Explore Offerings")
            )


if __name__ == "__main__":
    unittest.main()
