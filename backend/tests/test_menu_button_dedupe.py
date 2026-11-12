"""PDF/kit menu sanitize must not emit hyphen duplicates of vertical defaults."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fitz

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.auto_bifurcator import (  # noqa: E402
    AutoBifurcator,
    _attach_pdf_images_to_offerings,
    _menu_button_id,
    _menu_replacement_tombstones,
    _normalize_menu_button_key,
    _sanitize_menu_buttons,
)
from shared.tenancy.merge import merge_layers  # noqa: E402
from shared.tenancy.defaults import get_vertical_defaults  # noqa: E402
from bots.customer_b2c.menus import get_main_menu  # noqa: E402
from routes.offerings import _public, _public_media_url  # noqa: E402


class MenuButtonDedupeTests(unittest.TestCase):
    def test_records_only_expose_public_http_image_urls(self):
        self.assertIsNone(_public_media_url("paste https image URL"))
        self.assertIsNone(_public_media_url("/uploaded_files/image.png"))
        self.assertEqual(
            _public_media_url("https://imghipo.com/image.png"),
            "https://imghipo.com/image.png",
        )

    def test_records_keep_legacy_image_url_alias(self):
        record = _public({"image_url": "https://imghipo.com/image.png"})

        self.assertEqual(record["media_url"], "https://imghipo.com/image.png")
        self.assertEqual(record["image_url"], record["media_url"])

    def test_pdf_embedded_image_is_uploaded_and_attached_to_matching_offering(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Platinum Credit Card")
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
        pixmap.clear_with(240)
        page.insert_image(
            fitz.Rect(72, 100, 272, 300),
            stream=pixmap.tobytes("png"),
        )
        content = pdf.tobytes()
        pdf.close()

        offering = {"name": "Platinum Credit Card"}
        with patch(
            "services.image_upload._upload_to_imghippo",
            return_value="https://imghipo.com/platinum-card.png",
        ) as upload:
            _attach_pdf_images_to_offerings(
                [offering],
                [{"filename": "catalog.pdf", "ext": ".pdf", "_content": content}],
            )

        self.assertEqual(offering["media_url"], "https://imghipo.com/platinum-card.png")
        self.assertEqual(offering["media_type"], "image")
        upload.assert_called_once()

    def test_staged_pdf_offering_keeps_its_public_image_url(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Platinum Credit Card")
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
        pixmap.clear_with(240)
        page.insert_image(
            fitz.Rect(72, 100, 272, 300),
            stream=pixmap.tobytes("png"),
        )
        content = pdf.tobytes()
        pdf.close()

        bifurcator = AutoBifurcator("state-bank-of-india")
        with (
            patch.object(
                bifurcator,
                "bifurcate_with_llm",
                return_value={
                    "brand": {"name": "State Bank of India"},
                    "vertical": "banking",
                    "offerings": [{"name": "Platinum Credit Card"}],
                },
            ),
            patch(
                "services.image_upload._upload_to_imghippo",
                return_value="https://imghipo.com/platinum-card.png",
            ),
        ):
            staged = bifurcator.stage_files(
                [{"filename": "catalog.pdf", "content": content}]
            )

        self.assertEqual(
            staged["offerings"][0]["media_url"],
            "https://imghipo.com/platinum-card.png",
        )

    def test_pdf_image_is_not_uploaded_when_product_match_is_ambiguous(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Platinum Credit Card")
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
        pixmap.clear_with(240)
        image_bytes = pixmap.tobytes("png")
        page.insert_image(fitz.Rect(72, 100, 272, 300), stream=image_bytes)
        second_pixmap = fitz.Pixmap(
            fitz.csRGB,
            fitz.IRect(0, 0, 200, 200),
            False,
        )
        second_pixmap.clear_with(220)
        page.insert_image(
            fitz.Rect(300, 100, 500, 300),
            stream=second_pixmap.tobytes("png"),
        )
        content = pdf.tobytes()
        pdf.close()

        offering = {"name": "Platinum Credit Card"}
        with patch("services.image_upload._upload_to_imghippo") as upload:
            _attach_pdf_images_to_offerings(
                [offering],
                [{"filename": "catalog.pdf", "ext": ".pdf", "_content": content}],
            )

        self.assertNotIn("media_url", offering)
        upload.assert_not_called()

    def test_pdf_image_upload_failure_is_reported(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Platinum Credit Card")
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
        pixmap.clear_with(240)
        page.insert_image(
            fitz.Rect(72, 100, 272, 300),
            stream=pixmap.tobytes("png"),
        )
        content = pdf.tobytes()
        pdf.close()

        with patch("services.image_upload._upload_to_imghippo", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "public URL.*ImageHipo"):
                _attach_pdf_images_to_offerings(
                    [{"name": "Platinum Credit Card"}],
                    [{"filename": "catalog.pdf", "ext": ".pdf", "_content": content}],
                )

    def test_catalog_discards_placeholder_image_url_text(self):
        items = AutoBifurcator("state-bank-of-india").extract_catalog_section(
            {
                "tables": [[
                    ["Service / Offering", "Image URL"],
                    ["Platinum Credit Card", "paste https image URL"],
                ]]
            },
            vertical="banking",
        )

        self.assertEqual(len(items), 1)
        self.assertNotIn("media_url", items[0])

    def test_pdf_extractor_keeps_all_ten_menu_items(self):
        rows = [["Title", "Description", "Section", "Action"]]
        rows.extend(
            [f"Banking option {index}", f"Description {index}", "Banking", "faq"]
            for index in range(1, 11)
        )

        items = AutoBifurcator("state-bank-of-india").extract_menu_section(
            {"tables": [rows]},
            vertical="banking",
        )

        self.assertEqual(len(items), 10)
        self.assertEqual(items[-1]["title"], "Banking option 10")

    def test_pdf_menu_table_with_leading_section_marker_keeps_all_ten_items(self):
        rows = [
            ["[SECTION: WHATSAPP MENU]", "", "", ""],
            ["Button Title (<=24ch)", "Description (<=72ch)", "Section", "Action"],
            ["Banking Products", "Accounts, cards & loans", "Banking Products", "menu_products _ intent: menu_products _"],
            ["Cards", "Credit & debit card help", "Banking Products", "menu_cards _ intent: offerings"],
            ["Loans", "Personal, home & business loans", "Banking Products", "menu_loans _ intent: offerings"],
            ["Deposits & FDs", "Savings, fixed & recurring deposit rates", "Banking Products", "menu_deposits _ intent: offerings"],
            ["Check Eligibility", "Get a quick check", "Talk To Us", "menu_eligibility _ flow: book callback _"],
            ["Request a Callback", "Have us call you back", "Talk To Us", "menu_callback _ flow: book callback _"],
            ["Raise a Complaint", "Register a grievance", "Support", "menu_grievance _ flow: raise ticket _"],
            ["Branch & Support", "Find us / contact support", "Support", "menu_branch _ intent: branch locator _"],
            ["Talk to Human", "Chat with our team", "Support", "menu_human _ flow: book callback _"],
            ["Block Lost Card", "Instantly block lost or stolen card", "Support", "menu_block_card _ _ flow: raise ticket _"],
        ]
        bifurcator = AutoBifurcator("state-bank-of-india")

        sections = bifurcator.partition_sections(
            [{"text": "[SECTION: WHATSAPP MENU]", "tables": [rows]}]
        )
        items = bifurcator.extract_menu_section(sections["menu"], vertical="banking")
        buttons = _sanitize_menu_buttons(items, vertical="banking")

        self.assertEqual(len(sections["menu"]["tables"]), 1)
        self.assertEqual(len(items), 10)
        self.assertEqual(
            [item["title"] for item in items],
            [
                "Banking Products",
                "Cards",
                "Loans",
                "Deposits & FDs",
                "Check Eligibility",
                "Request a Callback",
                "Raise a Complaint",
                "Branch & Support",
                "Talk to Human",
                "Block Lost Card",
            ],
        )
        self.assertEqual(buttons[0]["id"], "menu_products")
        self.assertEqual(buttons[0]["intent"], "menu_products")
        self.assertEqual(buttons[7]["intent"], "branch_locator")
        self.assertEqual(buttons[-1]["flow"], "raise_ticket")

    def test_ten_extracted_menu_items_render_as_ten_whatsapp_rows(self):
        rows = [["Title", "Description", "Section", "Action"]]
        rows.extend(
            [f"Banking option {index}", f"Description {index}", "Banking", "faq"]
            for index in range(1, 11)
        )
        extracted = AutoBifurcator("state-bank-of-india").extract_menu_section(
            {"tables": [rows]},
            vertical="banking",
        )
        buttons = _sanitize_menu_buttons(extracted, vertical="banking")
        menu_buttons = [
            SimpleNamespace(
                id=button["id"],
                title=button["title"],
                description=button["description"],
                section=button["section"],
                icon="",
                sort_order=button["sort_order"],
                enabled=True,
            )
            for button in buttons
        ]

        def visible_buttons(features, in_business_hours=True):
            return menu_buttons

        profile = SimpleNamespace(
            tenant_id="state-bank-of-india",
            brand=SimpleNamespace(name="State Bank of India"),
            display_name="State Bank of India",
            features=SimpleNamespace(),
            menu=SimpleNamespace(
                body="Welcome",
                buttons=menu_buttons,
                visible_buttons=visible_buttons,
            ),
        )

        with (
            patch("shared.tenancy.resolver.resolve_effective_tenant_id", return_value="state-bank-of-india"),
            patch("shared.tenancy.loader.get_tenant_profile", return_value=profile),
        ):
            menu = get_main_menu("919500000000", tenant_id="state-bank-of-india")

        rendered_rows = [
            row
            for section in menu["sections"]
            for row in section["rows"]
        ]
        self.assertEqual(len(rendered_rows), 10)
        self.assertEqual(rendered_rows[-1]["title"], "Banking option 10")

    def test_pdf_menu_replaces_vertical_defaults_and_previous_custom_buttons(self):
        rows = [["Title", "Description", "Section", "Action"]]
        rows.extend(
            [f"Banking option {index}", f"Description {index}", "Banking", "faq"]
            for index in range(1, 11)
        )
        extracted = AutoBifurcator("state-bank-of-india").extract_menu_section(
            {"tables": [rows]},
            vertical="banking",
        )
        buttons = _sanitize_menu_buttons(extracted, vertical="banking")
        defaults = get_vertical_defaults("banking")
        default_buttons = defaults["menu"]["buttons"]
        previous_custom = {
            "id": "previous_custom_option",
            "title": "Old option",
            "description": "No longer in the PDF",
            "section": "Banking",
        }
        removals = _menu_replacement_tombstones(
            buttons,
            [*default_buttons, previous_custom],
            default_buttons,
        )

        published = merge_layers(
            merge_layers(
                defaults,
                {"menu": {"buttons": [*default_buttons, previous_custom]}},
            ),
            {"menu": {"buttons": [*buttons, *removals]}},
        )

        published_buttons = published["menu"]["buttons"]
        self.assertEqual(len(published_buttons), 10)
        self.assertEqual(
            [button["title"] for button in published_buttons],
            [f"Banking option {index}" for index in range(1, 11)],
        )

    def test_vertical_defaults_are_kept_when_extract_has_no_custom_options(self):
        defaults = get_vertical_defaults("banking")
        default_buttons = defaults["menu"]["buttons"]

        removals = _menu_replacement_tombstones(
            default_buttons,
            default_buttons,
            default_buttons,
        )

        self.assertEqual(removals, [])

    def test_publish_does_not_shadow_vertical_defaults_import(self):
        self.assertNotIn(
            "get_vertical_defaults",
            AutoBifurcator.apply_bifurcation.__code__.co_varnames,
        )

    def test_menu_button_id_uses_underscores(self):
        self.assertEqual(_menu_button_id("menu-products"), "menu_products")
        self.assertEqual(_menu_button_id("menu_products"), "menu_products")
        self.assertEqual(_menu_button_id("Banking Products"), "banking_products")

    def test_normalize_equates_hyphen_and_underscore(self):
        self.assertEqual(
            _normalize_menu_button_key("menu-products"),
            _normalize_menu_button_key("menu_products"),
        )

    def test_sanitize_collapses_hyphen_duplicates_onto_banking_defaults(self):
        raw = [
            {
                "id": "menu-products",
                "title": "Banking Products",
                "description": "Accounts, cards & loans",
                "section": "Products",
                "intent": "offerings",
            },
            {
                "id": "menu_products",
                "title": "Banking Products",
                "description": "Accounts, cards & loans",
                "section": "Products",
                "intent": "product_details",
            },
            {
                "id": "menu-cards",
                "title": "Cards",
                "description": "Credit & debit card help",
                "section": "Products",
                "action": "offerings",
            },
            {
                "id": "menu_loans",
                "title": "Loans",
                "description": "Personal, home & business loans",
                "section": "Products",
                "intent": "product_details",
            },
            {
                "id": "menu-loans",
                "title": "Loans",
                "description": "Personal, home & business loans",
                "section": "Products",
                "action": "offerings",
            },
        ]
        buttons = _sanitize_menu_buttons(raw, vertical="banking")
        ids = [b["id"] for b in buttons]
        titles = [b["title"] for b in buttons]

        self.assertEqual(ids.count("menu_products"), 1)
        self.assertEqual(ids.count("menu_cards"), 1)
        self.assertEqual(ids.count("menu_loans"), 1)
        self.assertNotIn("menu-products", ids)
        self.assertNotIn("menu-cards", ids)
        self.assertNotIn("menu-loans", ids)
        self.assertEqual(titles.count("Banking Products"), 1)
        self.assertEqual(titles.count("Cards"), 1)
        self.assertEqual(titles.count("Loans"), 1)
        self.assertEqual(len(buttons), 3)

    def test_merge_with_defaults_does_not_double_after_sanitize(self):
        raw = [
            {"id": "menu-products", "title": "Banking Products", "description": "x", "section": "Products"},
            {"id": "menu-cards", "title": "Cards", "description": "y", "section": "Products"},
            {"id": "menu-loans", "title": "Loans", "description": "z", "section": "Products"},
        ]
        draft_buttons = _sanitize_menu_buttons(raw, vertical="banking")
        defaults = get_vertical_defaults("banking")
        merged = merge_layers(
            defaults,
            {"menu": {"buttons": draft_buttons}},
        )
        products = [
            b for b in merged["menu"]["buttons"]
            if _normalize_menu_button_key(b.get("title", "")) == "banking_products"
            or _normalize_menu_button_key(b.get("id", "")) == "menu_products"
        ]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["id"], "menu_products")

    def test_merge_layers_collapses_raw_hyphen_duplicates_onto_defaults(self):
        """Draft/PDF layers with hyphen ids must not double Cards/Loans/Products."""
        defaults = get_vertical_defaults("banking")
        draft = {
            "menu": {
                "buttons": [
                    {
                        "id": "menu-products",
                        "title": "Banking Products",
                        "description": "Accounts, cards & loans",
                        "intent": "menu-products",
                    },
                    {
                        "id": "menu-cards",
                        "title": "Cards",
                        "description": "Credit & debit card help",
                        "intent": "offerings",
                    },
                    {
                        "id": "menu-loans",
                        "title": "Loans",
                        "description": "Personal, home & business loans",
                        "intent": "offerings",
                    },
                ]
            }
        }
        merged = merge_layers(defaults, draft)
        ids = [b["id"] for b in merged["menu"]["buttons"]]
        titles = [b["title"] for b in merged["menu"]["buttons"]]

        self.assertEqual(ids.count("menu_products"), 1)
        self.assertEqual(ids.count("menu_cards"), 1)
        self.assertEqual(ids.count("menu_loans"), 1)
        self.assertNotIn("menu-products", ids)
        self.assertNotIn("menu-cards", ids)
        self.assertNotIn("menu-loans", ids)
        self.assertEqual(titles.count("Banking Products"), 1)
        self.assertEqual(titles.count("Cards"), 1)
        self.assertEqual(titles.count("Loans"), 1)

    def test_merge_layers_collapses_both_forms_inside_one_layer(self):
        """A saved draft that already contains both id forms must still collapse."""
        layer = {
            "menu": {
                "buttons": [
                    {"id": "menu_products", "title": "Banking Products", "intent": "product_details"},
                    {"id": "menu_cards", "title": "Cards", "intent": "product_details"},
                    {"id": "menu-products", "title": "Banking Products", "intent": "menu-products"},
                    {"id": "menu_loans", "title": "Loans", "intent": "product_details"},
                    {"id": "menu-cards", "title": "Cards", "intent": "offerings"},
                    {"id": "menu-loans", "title": "Loans", "intent": "offerings"},
                ]
            }
        }
        merged = merge_layers({}, layer)
        ids = [b["id"] for b in merged["menu"]["buttons"]]
        titles = [b["title"] for b in merged["menu"]["buttons"]]
        self.assertEqual(ids, ["menu_products", "menu_cards", "menu_loans"])
        self.assertEqual(titles.count("Banking Products"), 1)
        self.assertEqual(titles.count("Cards"), 1)
        self.assertEqual(titles.count("Loans"), 1)

    def test_merge_layers_collapses_same_title_different_ids_onto_defaults(self):
        """PDF slug ids like explore_properties must not double default titles."""
        defaults = get_vertical_defaults("real_estate")
        draft = {
            "menu": {
                "buttons": [
                    {
                        "id": "explore-properties",
                        "title": "Explore Properties",
                        "description": "Browse listings",
                    },
                    {
                        "id": "schedule-site-visit",
                        "title": "Schedule Site Visit",
                        "description": "Book a visit",
                    },
                    {
                        "id": "customer-support",
                        "title": "Customer Support",
                        "description": "Get help",
                    },
                ]
            }
        }
        merged = merge_layers(defaults, draft)
        titles = [b["title"] for b in merged["menu"]["buttons"]]
        ids = [b["id"] for b in merged["menu"]["buttons"]]
        self.assertEqual(titles.count("Explore Properties"), 1)
        self.assertEqual(titles.count("Schedule Site Visit"), 1)
        self.assertEqual(titles.count("Customer Support"), 1)
        self.assertIn("menu_properties", ids)
        self.assertIn("menu_site_visit", ids)
        self.assertNotIn("explore_properties", ids)
        self.assertNotIn("explore-properties", ids)

    def test_it_kit_slugs_do_not_duplicate_services_and_technologies(self):
        """Leeway IT kit used our_services/technologies alongside menu_* defaults."""
        from shared.tenancy.schemas import MenuSpec, TenantProfile

        defaults = get_vertical_defaults("it_software")
        draft = {
            "menu": {
                "buttons": [
                    {
                        "id": "our_services",
                        "title": "Our Services",
                        "description": "AI, software, web, mobile & CPaaS",
                        "section": "Services",
                        "intent": "offerings",
                    },
                    {
                        "id": "technologies",
                        "title": "Technologies",
                        "description": "React, Python, Flutter, cloud & AI",
                        "section": "Services",
                        "intent": "technologies",
                    },
                    {
                        "id": "projects",
                        "title": "Projects",
                        "description": "Clients, portfolio & industries served",
                        "section": "Company",
                        "intent": "projects",
                    },
                ]
            }
        }
        merged = merge_layers(defaults, draft)
        titles = [b["title"] for b in merged["menu"]["buttons"]]
        ids = [b["id"] for b in merged["menu"]["buttons"]]
        self.assertEqual(titles.count("Our Services"), 1)
        self.assertEqual(titles.count("Technologies"), 1)
        self.assertIn("menu_services", ids)
        self.assertIn("menu_technologies", ids)
        self.assertNotIn("our_services", ids)
        self.assertNotIn("technologies", ids)

        # Toxic published snapshot that already contains both id forms.
        toxic = {
            "vertical": "it_software",
            "menu": {
                "buttons": [
                    {
                        "id": "menu_services",
                        "title": "Our Services",
                        "section": "🛠️ Services",
                        "icon": "🛠️",
                        "intent": "offerings",
                    },
                    {
                        "id": "menu_technologies",
                        "title": "Technologies",
                        "section": "🛠️ Services",
                        "icon": "💻",
                        "intent": "technologies",
                    },
                    {
                        "id": "our_services",
                        "title": "Our Services",
                        "section": "Services",
                        "intent": "offerings",
                    },
                    {
                        "id": "technologies",
                        "title": "Technologies",
                        "section": "Services",
                        "intent": "technologies",
                    },
                ]
            },
        }
        profile = TenantProfile(tenant_id="leewaysoftech", **merge_layers(defaults, toxic))
        profile_titles = [b.title for b in profile.menu.buttons]
        profile_ids = [b.id for b in profile.menu.buttons]
        self.assertEqual(profile_titles.count("Our Services"), 1)
        self.assertEqual(profile_titles.count("Technologies"), 1)
        self.assertIn("menu_services", profile_ids)
        self.assertIn("menu_technologies", profile_ids)
        self.assertNotIn("our_services", profile_ids)
        self.assertNotIn("technologies", profile_ids)

        # MenuSpec alone must collapse even without merge_layers.
        solo = MenuSpec(
            buttons=[
                {"id": "menu_services", "title": "Our Services", "intent": "offerings"},
                {"id": "our_services", "title": "Our Services", "intent": "offerings"},
                {"id": "technologies", "title": "Technologies", "intent": "technologies"},
                {"id": "menu_technologies", "title": "Technologies", "intent": "technologies"},
            ]
        )
        self.assertEqual([b.id for b in solo.buttons], ["menu_services", "menu_technologies"])

    def test_vertical_default_fill_keeps_banking_finance_ecommerce_targets(self):
        """Default fill must keep real intents/flows — not feature flags or enquire→callback."""
        cases = {
            "banking": {
                "menu_products": {"intent": "product_details", "flow": None, "req": "products_schemes"},
                "menu_cards": {"intent": "product_details", "flow": None, "req": "offering_details"},
                "menu_eligibility": {"intent": None, "flow": "enquire", "req": "lead_capture"},
                "menu_branch": {"intent": "branch_locator", "flow": None, "req": None},
                "menu_human": {"intent": None, "flow": "book_callback", "req": "human_handover"},
            },
            "finance": {
                "menu_services": {"intent": "service_enquiry", "flow": None, "req": "services"},
                "menu_faq": {"intent": "compliance_query", "flow": None, "req": "faq"},
                "menu_proposal": {"intent": None, "flow": "get_quote", "req": "quote"},
                "menu_human": {"intent": None, "flow": "book_callback", "req": "human_handover"},
            },
            "ecommerce": {
                "menu_products": {"intent": "product_details", "flow": None, "req": "products"},
                "menu_new_arrivals": {"intent": "new_arrivals", "flow": None, "req": None},
                "menu_about": {"intent": "policy_query", "flow": None, "req": None},
                "menu_view_cart": {"intent": None, "flow": None, "req": "cart"},
                "menu_human": {"intent": None, "flow": "book_callback", "req": "human_handover"},
            },
        }
        for vertical, expected in cases.items():
            with self.subTest(vertical=vertical):
                items = AutoBifurcator("demo").extract_menu_section(
                    {"text": "", "tables": []},
                    vertical=vertical,
                )
                buttons = _sanitize_menu_buttons(items, vertical=vertical)
                by_id = {b["id"]: b for b in buttons}
                defaults = get_vertical_defaults(vertical)["menu"]["buttons"]
                self.assertEqual(len(buttons), min(10, len(defaults)))
                for bid, want in expected.items():
                    self.assertIn(bid, by_id, f"{vertical} missing {bid}")
                    got = by_id[bid]
                    self.assertEqual(got.get("intent"), want["intent"], f"{vertical}/{bid} intent")
                    self.assertEqual(got.get("flow"), want["flow"], f"{vertical}/{bid} flow")
                    self.assertEqual(
                        got.get("requires_feature"),
                        want["req"],
                        f"{vertical}/{bid} requires_feature",
                    )

    def test_enquire_action_does_not_become_book_callback(self):
        buttons = _sanitize_menu_buttons(
            [{
                "id": "menu_eligibility",
                "title": "Check Eligibility",
                "description": "Get a quick check",
                "section": "Talk To Us",
                "action": "enquire",
            }],
            vertical="banking",
        )
        self.assertEqual(buttons[0]["flow"], "enquire")
        self.assertNotEqual(buttons[0].get("flow"), "book_callback")


if __name__ == "__main__":
    unittest.main()
