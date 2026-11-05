"""
QA Section 7 — Mobile/name/pincode/address/qty validators + cross-path disagreement matrix.
Pure unit evidence (no product mutation). Writes reports/qa/section7_*.json
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "routing"))

from backend.services.shared_validators import (  # noqa: E402
    validate_mobile,
    validate_name,
    validate_pincode,
    validate_address,
    validate_qty,
    check_serviceability,
)
from backend.services.bifurcation_extract import _normalize_phone  # noqa: E402
from shared.tenancy.schemas import (  # noqa: E402
    extract_first_valid_phone,
    is_valid_phone_input,
    _require_phone,
)

OUT = ROOT / "reports" / "qa"


class Section7Validators(unittest.TestCase):
    def test_mobile_matrix_and_disagreements(self):
        cases = [
            ("9876543210", True, "+919876543210"),
            ("+919876543210", True, "+919876543210"),
            ("+91 98765 43210", True, "+919876543210"),
            ("919876543210", True, "+919876543210"),
            ("09876543210", True, "+919876543210"),
            ("(098) 765 43210", True, "+919876543210"),
            ("+91-98765-43210", True, "+919876543210"),
            ("  9876543210  ", True, "+919876543210"),
            ("5876543210", False, None),
            ("1234567890", False, None),
            ("987654321", False, None),
            ("0987654321", False, None),
            ("98765432109", False, None),
            ("91987654321", False, None),
            ("+91987654321", False, None),
            ("", False, None),
            ("   ", False, None),
            ("abcdefghij", False, None),
            ("98765abc10", False, None),
            ("+91", False, None),
            ("0", False, None),
            ("+1 202 555 0123", False, None),
            ("🇮🇳9876543210", False, None),  # flag emoji
            ("9876543210x123", False, None),
        ]
        rows = []
        disagreements = []
        for raw, expect_valid, expect_norm in cases:
            vm = validate_mobile(raw)
            iv = is_valid_phone_input(raw)
            bn = _normalize_phone(raw)
            ex = extract_first_valid_phone(raw)
            # profile-style: try _require_phone only when non-empty
            req_ok = None
            req_err = None
            try:
                if raw and raw.strip():
                    out = _require_phone(raw, "test.phone")
                    req_ok = True
                    req_val = out
                else:
                    req_ok = True
                    req_val = raw
            except ValueError as e:
                req_ok = False
                req_val = None
                req_err = str(e)

            row = {
                "input": raw,
                "expect_valid": expect_valid,
                "expect_norm": expect_norm,
                "validate_mobile": vm,
                "is_valid_phone_input": iv,
                "bifurcation_normalize_phone": bn,
                "extract_first_valid_phone": ex,
                "require_phone_ok": req_ok,
                "require_phone_val": req_val,
                "require_phone_err": req_err,
            }
            # Pass/fail vs shared_validators expected matrix
            actual_valid = bool(vm.get("valid"))
            row["verdict_vs_expected"] = "PASS" if actual_valid == expect_valid and (
                (not expect_valid) or vm.get("normalized") == expect_norm
            ) else "FAIL"
            rows.append(row)

            # Disagreement: boolean accept differs across paths
            accepts = {
                "validate_mobile": actual_valid,
                "is_valid_phone_input": bool(iv),
                "bifurcation_normalize_phone_nonempty": bool(bn),
                "extract_first_valid_phone_nonempty": bool(ex),
                "require_phone": bool(req_ok) and (not raw.strip() or bool(req_val)),
            }
            # For empty input, require_phone returns value without validating — note separately
            uniq = set(accepts.values())
            if len(uniq) > 1:
                disagreements.append({"input": raw, "accepts": accepts, "detail": row})

            if row["verdict_vs_expected"] == "FAIL":
                # still collect; assertion at end with summary
                pass

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "section7_mobile_matrix.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        (OUT / "section7_disagreements.json").write_text(
            json.dumps(disagreements, indent=2, default=str), encoding="utf-8"
        )

        fails = [r for r in rows if r["verdict_vs_expected"] == "FAIL"]
        # Record but assert core expected cases for shared_validators
        self.assertEqual(
            fails,
            [],
            msg=f"validate_mobile matrix failures: {json.dumps(fails, indent=2)[:2000]}",
        )
        # Disagreements are bugs by themselves — fail the suite so they surface
        self.assertEqual(
            disagreements,
            [],
            msg=f"Validator disagreements found ({len(disagreements)}). See section7_disagreements.json",
        )

    def test_name_pincode_address_qty(self):
        name_cases = [
            ("A", False),
            ("Ab", True),
            ("A" * 60, True),
            ("A" * 61, False),
            ("12345", False),
            ("😀😀😀", False),
            ("<script>alert(1)</script>", False),  # sanitized may strip to empty/invalid
            ("John Doe", True),
        ]
        name_rows = []
        for raw, expect in name_cases:
            res = validate_name(raw)
            name_rows.append({"input": raw, "expect": expect, "result": res, "pass": res["valid"] == expect})
        (OUT / "section7_name.json").write_text(json.dumps(name_rows, indent=2), encoding="utf-8")

        pin_cases = [
            ("380001", True),
            ("080001", False),
            ("38000", False),
            ("3800011", False),
            ("38A001", False),
        ]
        pin_rows = [{"input": p, "result": validate_pincode(p)} for p, _ in pin_cases]
        for (p, expect), row in zip(pin_cases, pin_rows):
            row["expect"] = expect
            row["pass"] = row["result"]["valid"] == expect
        (OUT / "section7_pincode.json").write_text(json.dumps(pin_rows, indent=2), encoding="utf-8")

        svc = {
            "380001": check_serviceability("380001"),
            "110001": check_serviceability("110001"),
            "999999": check_serviceability("999999"),
        }
        (OUT / "section7_serviceability.json").write_text(json.dumps(svc, indent=2), encoding="utf-8")

        addr = [
            ("123456789", False),
            ("1234567890", True),
            ("A" * 250, True),
            ("A" * 251, False),
            ("Line1\nLine2 street", True),
            ("<b>XSS</b> House 12 Street", True),
        ]
        addr_rows = []
        for raw, expect in addr:
            res = validate_address(raw)
            addr_rows.append({"input": raw[:40], "len": len(raw), "expect": expect, "result": res, "pass": res["valid"] == expect})
        (OUT / "section7_address.json").write_text(json.dumps(addr_rows, indent=2), encoding="utf-8")

        qty = [
            ("0", 1, 99, None, False),
            ("-1", 1, 99, None, False),
            ("1", 1, 99, None, True),
            ("99", 1, 99, None, True),
            ("100", 1, 99, None, False),
            ("2", 5, 99, None, False),
            ("10", 1, 99, 5, False),
            ("2.5", 1, 99, None, False),
            ("two", 1, 99, None, False),
            ("007", 1, 99, None, True),
            ("999999999", 1, 99, None, False),
        ]
        qty_rows = []
        for raw, moq, mx, stock, expect in qty:
            res = validate_qty(raw, moq=moq, max_qty=mx, stock=stock)
            qty_rows.append({"input": raw, "expect": expect, "result": res, "pass": res["valid"] == expect})
        (OUT / "section7_qty.json").write_text(json.dumps(qty_rows, indent=2), encoding="utf-8")

        bad = [r for r in name_rows + pin_rows + addr_rows + qty_rows if not r.get("pass")]
        self.assertEqual(bad, [], msg=json.dumps(bad, indent=2)[:2000])


if __name__ == "__main__":
    unittest.main(verbosity=2)
