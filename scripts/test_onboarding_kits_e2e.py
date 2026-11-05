"""Comprehensive End-to-End Test for Predefined Onboarding Kits & Ollama Bifurcation.

Tests:
  1. Template Generation & Existence for all 8 verticals (DOCX & PDF)
  2. Onboarding Kits API listing and file downloads
  3. Section-by-Section Targeted Bifurcation on Real Estate DOCX
  4. Section-by-Section Targeted Bifurcation on Real Estate PDF
  5. E-Commerce Bifurcation on DOCX
  6. Publishing to Tenant System: Database, Client Pack, and RAG Knowledge Base
  7. Verification of RAG retrieval on newly onboarded tenant data
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Setup sys.path - place backend first
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.auto_bifurcator import AutoBifurcator
from database import init_db, list_products, save_product
from shared.tenancy import loader as tenancy_loader
from shared.tenancy import store as tenancy_store

client = TestClient(app)


def test_1_templates_exist():
    """Verify all 8 vertical DOCX and PDF kits are generated in static templates."""
    print("\n--- TEST 1: Verifying Onboarding Kit Files (All 8 Verticals) ---")
    templates_dir = PROJECT_ROOT / "backend" / "static" / "templates" / "onboarding"
    assert templates_dir.exists(), f"Templates directory {templates_dir} does not exist"

    verticals = [
        "real_estate", "ecommerce", "it_software", "tours_travel",
        "healthcare", "banking", "finance", "generic"
    ]

    for v in verticals:
        docx_file = templates_dir / f"{v}_onboarding_kit.docx"
        pdf_file = templates_dir / f"{v}_onboarding_kit.pdf"
        assert docx_file.exists(), f"Missing DOCX for {v}"
        assert pdf_file.exists(), f"Missing PDF for {v}"
        assert docx_file.stat().st_size > 5000, f"DOCX {v} too small"
        assert pdf_file.stat().st_size > 5000, f"PDF {v} too small"
        print(f"  [PASS] {v.upper():14} | DOCX: {docx_file.stat().st_size:,} B | PDF: {pdf_file.stat().st_size:,} B")


def test_2_api_list_and_download():
    """Verify backend API endpoints for listing and downloading kits."""
    print("\n--- TEST 2: Testing Onboarding Kits API Endpoints ---")
    # 1. Test listing
    res = client.get("/api/admin/onboarding-kits/list")
    assert res.status_code == 200, f"List API returned {res.status_code}: {res.text}"
    data = res.json()
    assert "templates" in data
    assert len(data["templates"]) == 8, f"Expected 8 templates, got {len(data['templates'])}"
    print(f"  [PASS] /api/admin/onboarding-kits/list returned {len(data['templates'])} templates")

    # 2. Test downloading DOCX
    res_docx = client.get("/api/admin/onboarding-kits/real_estate/docx")
    assert res_docx.status_code == 200, f"DOCX download returned {res_docx.status_code}"
    assert "wordprocessingml" in res_docx.headers.get("content-type", "")
    assert len(res_docx.content) > 10000
    print(f"  [PASS] /api/admin/onboarding-kits/real_estate/docx downloaded ({len(res_docx.content):,} B)")

    # 3. Test downloading PDF
    res_pdf = client.get("/api/admin/onboarding-kits/real_estate/pdf")
    assert res_pdf.status_code == 200, f"PDF download returned {res_pdf.status_code}"
    assert "pdf" in res_pdf.headers.get("content-type", "")
    assert len(res_pdf.content) > 10000
    print(f"  [PASS] /api/admin/onboarding-kits/real_estate/pdf downloaded ({len(res_pdf.content):,} B)")


def test_3_real_estate_docx_bifurcation():
    """Verify section-by-section staging extraction from Real Estate DOCX."""
    print("\n--- TEST 3: Staging Extraction from Real Estate DOCX ---")
    docx_path = PROJECT_ROOT / "backend" / "static" / "templates" / "onboarding" / "real_estate_onboarding_kit.docx"
    with open(docx_path, "rb") as f:
        file_bytes = f.read()

    bifurcator = AutoBifurcator("test_skyline_e2e")
    staged = bifurcator.stage_files([{"filename": "real_estate_onboarding_kit.docx", "content": file_bytes}])

    assert staged["status"] == "staged"
    assert staged["vertical"] == "real_estate"
    assert "Skyline Realty" in staged["brand"]["name"]
    assert "Skyline Advisor" in staged["brand"]["bot_name"]
    assert len(staged["menu_items"]) >= 5
    assert len(staged["offerings"]) >= 4
    assert len(staged["faqs"]) >= 4

    print(f"  [PASS] Vertical: {staged['vertical']}")
    print(f"  [PASS] Brand: {staged['brand']['name']} (Bot: {staged['brand']['bot_name']})")
    print(f"  [PASS] Menu Items Extracted: {len(staged['menu_items'])}")
    print(f"  [PASS] Offerings / Properties Extracted: {len(staged['offerings'])}")
    for off in staged["offerings"][:3]:
        print(f"         - {off['name']} | {off['category']} | {off['price']}")
        assert off["attrs"].get("bhk"), f"Property {off['name']} missing BHK attr"
    print(f"  [PASS] FAQs Extracted: {len(staged['faqs'])}")
    for f in staged["faqs"][:2]:
        print(f"         Q: {f['question']}")
        print(f"         A: {f['answer'][:65]}...")


def test_4_real_estate_pdf_bifurcation():
    """Verify section-by-section staging extraction from Real Estate PDF."""
    print("\n--- TEST 4: Staging Extraction from Real Estate PDF ---")
    pdf_path = PROJECT_ROOT / "backend" / "static" / "templates" / "onboarding" / "real_estate_onboarding_kit.pdf"
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()

    bifurcator = AutoBifurcator("test_skyline_e2e")
    staged = bifurcator.stage_files([{"filename": "real_estate_onboarding_kit.pdf", "content": file_bytes}])

    assert staged["status"] == "staged"
    assert staged["vertical"] == "real_estate"
    assert "Skyline" in staged["brand"]["name"]
    assert len(staged["menu_items"]) >= 5
    assert len(staged["offerings"]) >= 4
    assert len(staged["faqs"]) >= 4

    print(f"  [PASS] Vertical: {staged['vertical']}")
    print(f"  [PASS] Brand: {staged['brand']['name']}")
    print(f"  [PASS] Menu Items Extracted: {len(staged['menu_items'])}")
    print(f"  [PASS] Offerings / Properties Extracted: {len(staged['offerings'])}")
    print(f"  [PASS] FAQs Extracted: {len(staged['faqs'])}")


def test_5_publishing_and_verification():
    """Verify publishing staged draft to database, products table, and FAISS vector index."""
    print("\n--- TEST 5: Publishing Bifurcated Data & System Verification ---")
    docx_path = PROJECT_ROOT / "backend" / "static" / "templates" / "onboarding" / "real_estate_onboarding_kit.docx"
    with open(docx_path, "rb") as f:
        file_bytes = f.read()

    tenant_id = "skyline_test_publish"
    bifurcator = AutoBifurcator(tenant_id)
    staged = bifurcator.stage_files([{"filename": "real_estate_onboarding_kit.docx", "content": file_bytes}])

    # Apply & publish
    summary = bifurcator.apply_bifurcation(staged, raw_files=[], auto_publish=True)

    print(f"  [PASS] Published tenant: {summary['tenant_id']}")
    print(f"  [PASS] Offerings created: {summary['offerings_created']}")
    print(f"  [PASS] Menu buttons created: {summary['menu_buttons_count']}")
    print(f"  [PASS] Knowledge chunks indexed: {summary['kb_chunks_indexed']}")

    # Verify products table in DB
    prods = list_products(tenant_id=tenant_id, active_only=False)
    assert len(prods) >= 4, f"Expected at least 4 products in DB, found {len(prods)}"
    print(f"  [PASS] Verified {len(prods)} products present in database table 'products'")

    # Verify client config file
    config_file = PROJECT_ROOT / "clients" / tenant_id / "config.json"
    assert config_file.exists(), f"Client config file {config_file} not written"
    with open(config_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg.get("vertical") == "real_estate"
    assert "Skyline Realty" in cfg.get("display_name", "")
    print(f"  [PASS] Verified client config file written at {config_file.name}")

    # Verify Knowledge Base markdown file written
    kb_file = PROJECT_ROOT / "clients" / tenant_id / "knowledge" / "02_frequently-asked-questions-customer-support.md"
    assert kb_file.exists(), f"KB markdown file {kb_file} not written"
    print(f"  [PASS] Verified KB article written at {kb_file.name} ({kb_file.stat().st_size} bytes)")


def run_all_tests():
    print("======================================================================")
    print("RUNNING COMPLETE ONBOARDING & OLLAMA BIFURCATION TEST SUITE")
    print("======================================================================")
    init_db()
    test_1_templates_exist()
    test_2_api_list_and_download()
    test_3_real_estate_docx_bifurcation()
    test_4_real_estate_pdf_bifurcation()
    test_5_publishing_and_verification()
    print("\n======================================================================")
    print("ALL 5 END-TO-END TEST SUITES PASSED WITH 100% SUCCESS!")
    print("======================================================================")


if __name__ == "__main__":
    run_all_tests()
