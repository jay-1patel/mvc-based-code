"""Menu buttons that start a flow must keep that flow's feature live."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.tenancy.schemas import TenantProfile  # noqa: E402


class MenuFlowFeatureSyncTests(unittest.TestCase):
    def test_custom_itinerary_enables_quote(self):
        profile = TenantProfile(
            tenant_id="makemytrip-india-private-limited",
            vertical="tours_travel",
            features={"quote": False, "callback": False, "lead_capture": True},
            menu={
                "buttons": [
                    {
                        "id": "custom_itinerary",
                        "title": "Custom Itinerary",
                        "flow": "get_quote",
                        "enabled": True,
                    },
                    {
                        "id": "speak_to_expert",
                        "title": "Speak to Expert",
                        "flow": "book_callback",
                        "enabled": True,
                    },
                ]
            },
            flows=[
                {"name": "get_quote", "requires_feature": "quote", "steps": []},
                {"name": "book_callback", "requires_feature": "callback", "steps": []},
            ],
        )
        self.assertTrue(profile.feature_on("quote"))
        self.assertTrue(profile.feature_on("callback"))

    def test_flow_for_button_starts_get_quote(self):
        from services import flow_runner

        profile = TenantProfile(
            tenant_id="t",
            vertical="tours_travel",
            features={"quote": False},
            menu={
                "buttons": [
                    {
                        "id": "custom_itinerary",
                        "title": "Custom Itinerary",
                        "flow": "get_quote",
                    }
                ]
            },
            flows=[{"name": "get_quote", "requires_feature": "quote", "steps": []}],
        )
        with mock.patch.object(flow_runner, "_profile", return_value=profile):
            self.assertEqual(
                flow_runner.flow_for_button("wa", "Custom Itinerary", tenant_id="t"),
                "get_quote",
            )


if __name__ == "__main__":
    unittest.main()
