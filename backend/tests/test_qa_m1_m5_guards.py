"""Tests for new brand-protection and junk-offering guards (M1 + M5)."""

import pytest
from services.bifurcation_guards import (
    is_junk_offering_name,
    is_placeholder_brand_value,
    merge_brand_fill_empty,
)


# ── M1 — Brand-name corruption prevention ───────────────────────────────────

class TestPlaceholderBrandValue:
    """Brand fields must never become PDF-section text."""

    def test_make_myt_trip_style_injection_rejected(self):
        # This exact pattern corrupted MakeMyTrip's brand.name
        val = "Booking Checklist Confirm destination, dates, and availability for all properties"
        assert is_placeholder_brand_value("name", val) is True

    def test_terms_conditions_rejected_as_brand_name(self):
        assert is_placeholder_brand_value("name", "Terms & Conditions") is True
        assert is_placeholder_brand_value("name", "Terms of Service") is True
        assert is_placeholder_brand_value("name", "Privacy Policy of Data") is True

    def test_long_paragraph_rejected(self):
        long_val = "Our company provides world-class real estate solutions across Bangalore with multiple premium projects featuring modern amenities and excellent connectivity."
        assert is_placeholder_brand_value("name", long_val) is True

    def test_real_brand_name_accepted(self):
        assert is_placeholder_brand_value("name", "Prestigue") is False
        assert is_placeholder_brand_value("name", "MakeMyTrip") is False
        assert is_placeholder_brand_value("name", "Tata Consultancy Services") is False
        assert is_placeholder_brand_value("name", "Gardenia Estates") is False

    def test_placeholder_defaults_still_rejected(self):
        assert is_placeholder_brand_value("name", "your company") is True
        assert is_placeholder_brand_value("name", "company name") is True
        assert is_placeholder_brand_value("bot_name", "assistant") is True
        assert is_placeholder_brand_value("website", "https://example.com") is True
        assert is_placeholder_brand_value("tagline", "your tagline") is True

    def test_faqs_heading_rejected(self):
        assert is_placeholder_brand_value("name", "Frequently Asked Questions") is True
        assert is_placeholder_brand_value("name", "FAQ Section") is True


# ── M5 — Junk offering name detection ──────────────────────────────────────

class TestIsJunkOfferingName:
    """Offerings must not be created from headings or structural noise."""

    def test_terms_conditions_is_junk(self):
        assert is_junk_offering_name("Terms & Conditions") is True

    def test_booking_checklist_is_junk(self):
        assert is_junk_offering_name("Booking Checklist Confirm destination") is True
        assert is_junk_offering_name("Checklist of Items") is True

    def test_faq_section_is_junk(self):
        assert is_junk_offering_name("Frequently Asked Questions") is True
        assert is_junk_offering_name("FAQs") is True

    def test_disclaimer_is_junk(self):
        assert is_junk_offering_name("Disclaimer") is True

    def test_sentence_length_noise_is_junk(self):
        # Long strings with many function words look like paragraphs
        val = "Confirm destination, dates, and availability for all properties booked"
        assert is_junk_offering_name(val) is True

    def test_real_offering_names_accepted(self):
        assert is_junk_offering_name("2 BHK Apartment") is False
        assert is_junk_offering_name("Villa Plot") is False
        assert is_junk_offering_name("IT Staffing Services") is False

    def test_numeric_and_short_are_junk(self):
        assert is_junk_offering_name("1") is True
        assert is_junk_offering_name("AB") is True  # too short
        assert is_junk_offering_name("Item") is True
        assert is_junk_offering_name("Product 1") is True


# ── merge_brand_fill_empty integration ─────────────────────────────────────

class TestMergeBrandFillEmpty:
    """Ensure junk brand values do NOT overwrite existing ones."""

    def test_junk_does_not_overwrite_existing_brand(self):
        existing = {"name": "Prestigue"}
        incoming = {"name": "Booking Checklist Confirm"}
        result = merge_brand_fill_empty(existing, incoming)
        assert result["name"] == "Prestigue"

    def test_empty_brand_gets_clean_value(self):
        existing = {}
        incoming = {"name": "MakeMyTrip"}
        result = merge_brand_fill_empty(existing, incoming)
        assert result["name"] == "MakeMyTrip"
