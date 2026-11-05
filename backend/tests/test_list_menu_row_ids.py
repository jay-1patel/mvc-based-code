"""Helpers around offerings categories / uniqueness (list-menu send guard)."""

import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from services import offerings_flow
import routing.whatsapp as whatsapp


class UniqueCategoryTests(unittest.TestCase):
    def test_unique_categories_dedupes_case(self):
        matched = offerings_flow._unique_categories(["General", "general", "Savings"])
        self.assertEqual(matched, ["General", "Savings"])


class SendGuardTests(unittest.TestCase):
    """The send2.digital sender is the last line of defence for list menus."""

    def test_duplicate_row_ids_are_made_unique_before_sending(self):
        sent = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, **kwargs):
                sent["payload"] = json
                raise RuntimeError("stop before the network call")

        sections = [{
            "title": "Select Category",
            "rows": [
                {"id": "cat_general", "title": "General", "description": "10 offerings"},
                {"id": "cat_general", "title": "General ", "description": "8 offerings"},
            ],
        }]
        with patch.object(whatsapp, "get_tenant_send2_credentials", lambda tenant_id=None: ("u", "p")), \
             patch.object(whatsapp.httpx, "Client", FakeClient):
            try:
                whatsapp.send_whatsapp_list_menu("919876543210", "h", "b", "Pick", sections)
            except RuntimeError:
                pass

        ids = [row["id"] for section in sent["payload"]["sections"] for row in section["rows"]]
        self.assertEqual(len(ids), len(set(ids)), f"duplicated row ids reached the API: {ids}")


if __name__ == "__main__":
    unittest.main()
