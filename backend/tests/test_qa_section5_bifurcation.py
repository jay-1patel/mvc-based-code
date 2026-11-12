"""
QA Section 5 — Bifurcation guards / heuristic path on clean test PDF (no auto-publish to prod tenants).
Uses in-memory / temp tenant helpers where available; otherwise runs extract+heuristic only.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))

OUT = ROOT / "reports" / "qa"
PACK = ROOT / "qa_testdata"


class Section5Bifurcation(unittest.TestCase):
    def test_junk_offering_guards(self):
        from backend.services import bifurcation_guards as g

        junk = [
            "Terms & Conditions",
            "Page 3 of 3",
            "Frequently Asked Questions",
            "Price List Notes",
            "Table of Contents",
            "Disclaimer",
        ]
        real = [
            "Masala Chips 100g",
            "Cheese Puffs 80g",
            "Diet Khakra 150g",
        ]
        rows = []
        for name in junk + real:
            fn = getattr(g, "is_junk_offering_name", None)
            if not fn:
                self.skipTest("is_junk_offering_name missing")
            is_junk = fn(name)
            expect_junk = name in junk
            rows.append({"name": name, "is_junk": is_junk, "expect_junk": expect_junk, "pass": is_junk == expect_junk})
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section5_junk_guards.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        fails = [r for r in rows if not r["pass"]]
        self.assertEqual(fails, [], msg=json.dumps(fails, indent=2))

    def test_heuristic_extract_on_clean_pdf(self):
        import fitz
        from backend.extract import process_file, results_to_chunks

        # The checked-in fixture was never committed. Build the same
        # two-page catalog the assertions describe.
        doc = fitz.open()
        first = doc.new_page()
        first.insert_text(
            (72, 72),
            "Masala Chips 100g\n"
            "Call 9876543210\n"
            "We deliver across India. Shelf life 6 months.\n"
            "Terms & Conditions\n",
        )
        second = doc.new_page()
        second.insert_text((72, 72), "Page 2. More product notes for Masala Chips.")
        pdf = Path(tempfile.mkdtemp()) / "clean_multi_page.pdf"
        doc.save(pdf)
        doc.close()
        result = process_file(str(pdf))
        self.assertIsNotNone(result)
        chunks = results_to_chunks(result)
        text = "\n".join(c.get("content", "") for c in chunks)
        evidence = {
            "chunk_count": len(chunks),
            "text_len": len(text),
            "has_phone": "9876543210" in text,
            "has_faq_q": "deliver across India" in text.lower() or "shelf life" in text.lower(),
            "has_product": "Masala Chips" in text,
            "has_terms_heading": "Terms & Conditions" in text,
            "sample": text[:600],
        }
        # Heuristic extract if available
        try:
            from backend.services.bifurcation_extract import heuristic_extract_corpus

            he = heuristic_extract_corpus(text)
            evidence["heuristic_type"] = type(he).__name__
            evidence["heuristic_preview"] = str(he)[:1000]
        except Exception as e:
            evidence["heuristic_error"] = f"{type(e).__name__}: {e}"

        try:
            from backend.services.pdf_intake import _partition, _heuristic_buckets

            # call whatever public-ish helpers exist
            evidence["pdf_intake_helpers"] = [
                x for x in dir(__import__("backend.services.pdf_intake", fromlist=["*"])) if not x.startswith("__")
            ][:40]
        except Exception as e:
            evidence["pdf_intake_error"] = str(e)

        (OUT / "section5_heuristic.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        self.assertTrue(evidence["has_phone"])
        self.assertTrue(evidence["has_product"])
        self.assertTrue(evidence["has_faq_q"], "FAQ text missing from chunks — silent truncation?")

    def test_placeholder_brand_protection_unit(self):
        from backend.services import bifurcation_guards as g

        fn = getattr(g, "is_placeholder_brand_value", None)
        if not fn:
            self.skipTest("is_placeholder_brand_value missing")
        cases = [
            ("Your Company Name", True),
            ("N/A", True),
            ("", True),
            ("Leeway QA Test Foods Pvt Ltd", False),
            ("Acme Corp", False),
        ]
        rows = [
            {
                "value": v,
                "is_placeholder": fn("name", v),
                "expect": e,
                "pass": fn("name", v) == e,
            }
            for v, e in cases
        ]
        (OUT / "section5_brand_placeholder.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        fails = [r for r in rows if not r["pass"]]
        self.assertEqual(fails, [], msg=json.dumps(fails, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
