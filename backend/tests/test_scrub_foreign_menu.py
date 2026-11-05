"""Foreign-vertical menu/intent rows must not survive on another vertical."""

from __future__ import annotations

import unittest

from shared.tenancy.defaults import (
    foreign_vertical_menu_button_ids,
    scrub_foreign_vertical_artifacts,
)


class ScrubForeignMenuTests(unittest.TestCase):
    def test_tours_drops_leeway_and_real_estate_rows(self):
        foreign = foreign_vertical_menu_button_ids("tours_travel")
        self.assertIn("menu_technologies", foreign)
        self.assertIn("menu_careers", foreign)
        self.assertIn("menu_services", foreign)
        self.assertIn("menu_properties", foreign)
        # Shared across travel + other packs — must stay.
        self.assertNotIn("menu_callback", foreign)
        self.assertNotIn("menu_human", foreign)
        self.assertNotIn("menu_brochure", foreign)
        self.assertNotIn("menu_packages", foreign)

    def test_scrub_removes_contaminated_buttons_and_intents(self):
        layer = {
            "vertical": "tours_travel",
            "menu": {
                "buttons": [
                    {"id": "menu_packages", "title": "Packages"},
                    {"id": "menu_technologies", "title": "Technologies"},
                    {"id": "menu_careers", "title": "Careers"},
                    {"id": "menu_properties", "title": "Explore Properties"},
                    {"id": "menu_custom_honeymoon", "title": "Honeymoon"},
                    {"id": "menu_callback", "title": "Book a Callback"},
                ]
            },
            "intents": [
                {"name": "package_details", "keywords": ["package"]},
                {"name": "technologies", "keywords": ["tech"]},
                {"name": "careers", "keywords": ["jobs"]},
                {"name": "property_listings", "keywords": ["bhk"]},
            ],
        }
        scrub_foreign_vertical_artifacts(layer, "tours_travel")
        ids = [b["id"] for b in layer["menu"]["buttons"]]
        self.assertEqual(
            ids,
            ["menu_packages", "menu_custom_honeymoon", "menu_callback"],
        )
        names = [i["name"] for i in layer["intents"]]
        self.assertEqual(names, ["package_details"])


if __name__ == "__main__":
    unittest.main()
