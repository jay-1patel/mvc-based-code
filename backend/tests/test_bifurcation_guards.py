"""Guards that stop wrong-vertical / junk offerings and brand overwrites."""

import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.bifurcation_guards import (  # noqa: E402
    filter_offerings_for_tenant,
    is_junk_offering_name,
    is_placeholder_brand_value,
    merge_brand_fill_empty,
    offering_matches_vertical,
    resolve_locked_vertical,
)
from shared.tenancy.schemas import (  # noqa: E402
    extract_first_url,
    extract_first_valid_phone,
    _require_phone,
    _require_url,
)

try:
    # Import the helpers through the facade to pin the re-export contract,
    # but patch product_slug_taken where _ensure_free_slug resolves it —
    # services.bifurcation_extract (its post-split home).
    from services.auto_bifurcator import _ensure_free_slug, _slugify  # noqa: E402
    from services import bifurcation_extract as _bi_mod  # noqa: E402

    HAS_BIFURCATOR = True
except Exception:  # heavy deps (fitz/docx/openpyxl) missing in test env
    HAS_BIFURCATOR = False


class JunkNameTests(unittest.TestCase):
    def test_rejects_numbers_and_placeholders(self):
        for name in ("1", "9", "42", "Item", "N/A", "Product 1", "ab"):
            self.assertTrue(is_junk_offering_name(name), name)

    def test_keeps_real_names(self):
        for name in ("Kashmir Paradise", "Goa Beach Getaway", "SBI Home Loan"):
            self.assertFalse(is_junk_offering_name(name), name)


class VerticalMatchTests(unittest.TestCase):
    def test_rejects_real_estate_into_travel(self):
        self.assertFalse(
            offering_matches_vertical(
                "Prestige Gardenia Estates",
                "Residential",
                "3 BHK apartment with RERA and carpet area",
                locked_vertical="tours_travel",
            )
        )

    def test_rejects_iot_into_banking(self):
        self.assertFalse(
            offering_matches_vertical(
                "Smart Irrigation System",
                "IoT Solutions",
                "Soil moisture sensor microcontroller irrigation dashboard",
                locked_vertical="banking",
            )
        )

    def test_keeps_travel_package(self):
        self.assertTrue(
            offering_matches_vertical(
                "Kashmir Paradise",
                "Domestic Tour",
                "5N/6D Srinagar Gulmarg holiday package with hotel",
                locked_vertical="tours_travel",
            )
        )

    def test_generic_accepts_all(self):
        self.assertTrue(
            offering_matches_vertical(
                "Prestige Gardenia Estates",
                "Residential",
                "3 BHK apartment",
                locked_vertical="generic",
            )
        )


class FilterOfferingsTests(unittest.TestCase):
    def test_filters_mixed_catalog(self):
        raw = [
            {"name": "Kashmir Paradise", "category": "Tour", "description": "5N holiday package hotel"},
            {"name": "Prestige Gardenia Estates", "category": "Residential", "description": "3 BHK RERA apartment"},
            {"name": "1", "category": "General", "description": ""},
            {"name": "Smart Irrigation System", "category": "IoT", "description": "sensor microcontroller"},
        ]
        accepted, rejected = filter_offerings_for_tenant(raw, locked_vertical="tours_travel")
        names = [o["name"] for o in accepted]
        self.assertEqual(names, ["Kashmir Paradise"])
        reasons = {r["name"]: r["reason"] for r in rejected}
        self.assertEqual(reasons["1"], "junk_name")
        self.assertEqual(reasons["Prestige Gardenia Estates"], "off_vertical")
        self.assertEqual(reasons["Smart Irrigation System"], "off_vertical")


class BrandMergeTests(unittest.TestCase):
    def test_fill_empty_only(self):
        existing = {
            "name": "MakeMyTrip Limited",
            "support_email": "care@makemytrip.com",
            "website": "",
        }
        incoming = {
            "name": "Hiren Dhandhalya IoT Assignment",
            "support_email": "iot@example.com",
            "website": "https://iot.example.com",
            "bot_name": "Smart Irrigation Assistant",
        }
        merged = merge_brand_fill_empty(existing, incoming, tenant_id="makemytrip")
        self.assertEqual(merged["name"], "MakeMyTrip Limited")
        self.assertEqual(merged["support_email"], "care@makemytrip.com")
        self.assertEqual(merged["website"], "https://iot.example.com")
        self.assertEqual(merged["bot_name"], "Smart Irrigation Assistant")

    def test_placeholder_detection(self):
        self.assertTrue(
            is_placeholder_brand_value("website", "https://www.makemytrip.com", "makemytrip")
        )
        self.assertFalse(
            is_placeholder_brand_value("website", "https://www.makemytrip.com/care", "makemytrip")
        )


class LockedVerticalTests(unittest.TestCase):
    def test_falls_back_to_extracted_without_tenant(self):
        self.assertEqual(resolve_locked_vertical("", "banking"), "banking")
        self.assertEqual(resolve_locked_vertical("", "not_a_vertical"), "generic")


class BrandContactValidationTests(unittest.TestCase):
    """sbi publish crashed on multi-value website/phone from the PDF extract."""

    def test_website_with_notes_extracts_first_url(self):
        dirty = "https://sbi.co.in (rates & product pages: https://bank.sbi)"
        self.assertEqual(_require_url(dirty, "brand.website"), "https://sbi.co.in")
        self.assertEqual(extract_first_url(dirty), "https://sbi.co.in")

    def test_clean_urls_pass_through(self):
        for url in ("https://sbi.co.in", "http://www.example.com/rates.html", "sbi.co.in"):
            self.assertTrue(_require_url(url, "brand.website"))

    def test_garbage_url_still_raises(self):
        with self.assertRaises(ValueError):
            _require_url("not a url at all", "brand.website")

    def test_multi_phone_extracts_one(self):
        dirty = "1800 425 3800 · +91 80 26599990 (overseas)"
        self.assertEqual(_require_phone(dirty, "brand.support_phone"), "+91 80 26599990")
        self.assertEqual(extract_first_valid_phone(dirty), "+91 80 26599990")


@unittest.skipUnless(HAS_BIFURCATOR, "auto_bifurcator deps unavailable")
class SlugCollisionTests(unittest.TestCase):
    """products.slug is UNIQUE across tenants — inserts must not collide."""

    def test_suffixes_when_taken_by_same_tenant(self):
        orig = _bi_mod.product_slug_taken
        try:
            _bi_mod.product_slug_taken = lambda s, t=None: False
            used = set()
            first = _ensure_free_slug(_slugify("Personal Loan"), used, "sbi")
            self.assertEqual(first, "personal-loan")

            # This tenant's catalog already holds the slug (tenant-scoped
            # UNIQUE(tenant_id, slug) since the 2026-10 migration):
            _bi_mod.product_slug_taken = lambda s, t=None: (s, t) == ("personal-loan", "sbi")
            second = _ensure_free_slug(_slugify("Personal Loan"), set(), "sbi")
            self.assertEqual(second, "personal-loan-2")
            # Another tenant holding the same slug must NOT force a suffix.
            third = _ensure_free_slug(_slugify("Personal Loan"), set(), "hdfc")
            self.assertEqual(third, "personal-loan")
        finally:
            _bi_mod.product_slug_taken = orig

    def test_in_batch_duplicates_get_distinct_slugs(self):
        orig = _bi_mod.product_slug_taken
        try:
            _bi_mod.product_slug_taken = lambda s, t=None: False
            used = set()
            a = _ensure_free_slug(_slugify("SBI FD"), used, "sbi")
            b = _ensure_free_slug(_slugify("SBI-FD"), used, "sbi")  # same slug after _slugify
            self.assertEqual(a, "sbi-fd")
            self.assertEqual(b, "sbi-fd-2")
        finally:
            _bi_mod.product_slug_taken = orig


if __name__ == "__main__":
    unittest.main()
