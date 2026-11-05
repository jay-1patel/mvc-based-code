"""
QA Section 4 — Document extraction completeness against qa_testdata pack.
"""
from __future__ import annotations

import json
import sys
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "reports" / "qa"
PACK = ROOT / "qa_testdata"


class Section4Extract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend import extract as ex

        cls.ex = ex

    def _flatten_text(self, result) -> str:
        if not result:
            return ""
        if isinstance(result, str):
            return result
        parts = []
        if isinstance(result, dict):
            for page in result.get("pages") or []:
                if isinstance(page, dict):
                    parts.append(page.get("text") or "")
            for para in result.get("paragraphs") or []:
                parts.append(para if isinstance(para, str) else str(para))
            for sheet in result.get("sheets") or []:
                parts.append(str(sheet))
            if result.get("text"):
                parts.append(str(result["text"]))
            if result.get("content"):
                parts.append(str(result["content"]))
            for table in result.get("tables") or []:
                parts.append(str(table))
        return "\n".join(parts)

    def _run(self, name: str, path: Path):
        row = {"file": name, "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
        if not path.exists():
            row["verdict"] = "BLOCKED"
            row["reason"] = "missing file"
            return row
        try:
            result = self.ex.process_file(str(path))
            row["result_type"] = type(result).__name__
            flat = self._flatten_text(result)
            row["text_len"] = len(flat)
            row["text_sample"] = flat[:500]
            if isinstance(result, dict):
                row["keys"] = list(result.keys())
                row["pages_len"] = len(result.get("pages") or [])
                row["tables_len"] = len(result.get("tables") or [])
            row["verdict"] = "PASS" if result is not None else "FAIL"
            row["error"] = None if result is not None else "process_file returned None"
        except Exception as e:
            row["verdict"] = "FAIL"
            row["error"] = f"{type(e).__name__}: {e}"
            row["traceback"] = traceback.format_exc()[-800:]
        return row

    def test_extraction_pack(self):
        files = [
            "clean_multi_page.pdf",
            "scanned_ocr.pdf",
            "embedded_images.pdf",
            "table_spanning_pages.pdf",
            "large_bulky.pdf",
            "empty_no_text.pdf",
            "password_protected.pdf",
            "corrupted.pdf",
            "lie_extension.pdf",
            "same_content.docx",
            "same_content.txt",
            "same_content.md",
            "same_content.csv",
            "same_content.xlsx",
            "zero_byte.pdf",
        ]
        rows = []
        for name in files:
            rows.append(self._run(name, PACK / name))

        # Completeness checks on clean PDF
        clean = next(r for r in rows if r["file"] == "clean_multi_page.pdf")
        sample = (clean.get("text_sample") or clean.get("result_preview") or "").lower()
        completeness = {
            "has_brand": "leeway" in sample,
            "has_phone": "9876543210" in sample,
            "has_email": "qa-support" in sample,
            "has_price": "45.00" in sample or "45" in sample,
            "has_faq": "deliver" in sample or "shelf" in sample,
        }
        clean["completeness"] = completeness
        if clean.get("verdict") == "PASS" and not all(completeness.values()):
            clean["verdict"] = "FAIL"
            clean["error"] = f"incomplete extraction: {completeness}"

        # Password / corrupt should not hang forever and should surface error or empty
        for name in ("password_protected.pdf", "corrupted.pdf"):
            r = next(x for x in rows if x["file"] == name)
            if r.get("verdict") == "PASS" and not r.get("error"):
                # silent success with empty/garbage is a silent-failure candidate
                preview = (r.get("text_sample") or r.get("result_preview") or "")
                if "Secret product" in preview or "999" in preview:
                    r["verdict"] = "FAIL"
                    r["error"] = "password-protected content extracted without password"
                    r["silent_failure"] = True

        # OCR path
        scanned = next(r for r in rows if r["file"] == "scanned_ocr.pdf")
        scanned_text = (scanned.get("text_sample") or scanned.get("result_preview") or "").lower()
        if "tesseract" in (scanned.get("error") or "").lower() or "ocr" in (scanned.get("error") or "").lower():
            scanned["note"] = "OCR dependency missing — graceful error"
        elif "scanned snacks" not in scanned_text and "9988776655" not in scanned_text:
            scanned["ocr_missing_content"] = True
            scanned["silent_failure_candidate"] = True

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section4_extract.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

        hard_fails = [r for r in rows if r.get("verdict") == "FAIL" and r["file"] == "clean_multi_page.pdf"]
        self.assertEqual(hard_fails, [], msg=json.dumps(hard_fails, indent=2)[:1500])


if __name__ == "__main__":
    unittest.main(verbosity=2)
