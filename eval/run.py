"""
Headless eval harness (Phase 0.6).

Drives the profile-driven intent pipeline over eval/leeway_eval.jsonl without
WhatsApp, Ollama, or any live LLM: rules first, embeddings (bge-m3) second.
Deterministic on every run, so it can gate deploys.

Each JSONL item:
    utterance   what the user says
    expected    intent name, or "any" when only the negative assertions matter
    expect_flow optional: the flow the matched intent must start (after the
                out-of-hours variant is applied)
    expect_not  optional: intents the matcher must NEVER return (commerce
                leakage probes for a non-commerce tenant)

Usage:
    python eval/run.py                          # report, exit 0
    python eval/run.py --min-accuracy 90        # exit 1 below 90% (CI gate)
    python eval/run.py --tenant leewaysoftech --dataset eval/leeway_eval.jsonl

The 90% hard gate activates with the mature Leeway dataset (Phase 5); until
then this runs as a report.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))
sys.path.append(str(ROOT / "backend" / "services"))


def load_dataset(path: Path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{line_no}: invalid JSON — {e}")
    return items


def run(tenant_id: str, dataset_path: Path, use_embeddings: bool = True):
    from shared.tenancy.loader import get_tenant_profile
    from shared.tenancy.intent_match import match_intent

    profile = get_tenant_profile(tenant_id)
    items = load_dataset(dataset_path)

    results = []
    for item in items:
        utt = item["utterance"]
        predicted, score, tier = match_intent(utt, profile, use_embeddings=use_embeddings)
        rec = {
            "utterance": utt,
            "expected": item.get("expected", "any"),
            "predicted": predicted or "none",
            "tier": tier,
            "score": round(score, 3),
            "ok": True,
            "reason": "",
        }

        if item.get("expected") and item["expected"] != "any":
            if predicted != item["expected"]:
                rec["ok"] = False
                rec["reason"] = f"expected {item['expected']}, got {predicted or 'none'}"

        for banned in item.get("expect_not", []):
            if predicted == banned:
                rec["ok"] = False
                rec["reason"] = f"commerce intent {banned} leaked for a non-commerce tenant"
            if banned in profile.active_intent_names():
                rec["ok"] = False
                rec["reason"] = f"intent {banned} is ACTIVE in the profile"

        results.append(rec)

    # Flow assertions: the matched intent's flow must actually start.
    flow_probes = [
        (i, r) for i, r in zip(items, results)
        if i.get("expect_flow") and r["ok"]
    ]
    flow_results = []
    for item, rec in flow_probes:
        expected_flow = item["expect_flow"]
        from shared.tenancy.hours import select_flow_name
        # The out-of-hours variant may legally swap the flow; the assertion is
        # that the variant resolution + runner start the right one.
        resolved = select_flow_name(tenant_id, expected_flow)
        from backend.services import flow_runner
        wa_id = f"evalprobe-{abs(hash(item['utterance'])) % 10**8}"
        outcome = flow_runner.start_flow(wa_id, resolved, tenant_id=tenant_id)
        ok = bool(outcome.get("handled")) and outcome.get("flow") == resolved
        # Clean up the probe user's state so the eval leaves no residue.
        try:
            flow_runner._state().clear_flow(wa_id)
        except Exception:
            logger.exception("EVAL_PROBE_CLEANUP_FAILED | wa=%s", wa_id)
        flow_results.append({
            "utterance": item["utterance"],
            "flow": resolved,
            "ok": ok,
            "reason": "" if ok else f"start_flow returned {outcome.get('error', outcome.get('flow'))}",
        })

    passed = sum(1 for r in results if r["ok"]) + sum(1 for r in flow_results if r["ok"])
    total = len(results) + len(flow_results)
    accuracy = 100.0 * passed / total if total else 0.0
    return {
        "tenant": tenant_id,
        "vertical": profile.vertical,
        "dataset": str(dataset_path),
        "total": total,
        "passed": passed,
        "accuracy": round(accuracy, 1),
        "results": results,
        "flow_results": flow_results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="leewaysoftech")
    parser.add_argument("--dataset", default=str(ROOT / "eval" / "leeway_eval.jsonl"))
    parser.add_argument("--min-accuracy", type=float, default=0.0,
                        help="Exit 1 below this accuracy percentage (CI gate).")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Rules tier only — fast smoke, weaker signal.")
    parser.add_argument("--json", action="store_true", help="Print the raw report as JSON.")
    args = parser.parse_args()

    report = run(args.tenant, Path(args.dataset),
                 use_embeddings=not args.no_embeddings)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Eval report — tenant: {report['tenant']} (vertical: {report['vertical']})")
        print(f"  dataset: {report['dataset']}")
        print(f"  {report['passed']}/{report['total']} passed — accuracy {report['accuracy']}%")
        for r in report["results"]:
            if not r["ok"]:
                print(f"  FAIL [{r['tier']}] {r['utterance']!r}: {r['reason']} "
                      f"(got {r['predicted']}, score {r['score']})")
        for r in report["flow_results"]:
            if not r["ok"]:
                print(f"  FLOW FAIL {r['utterance']!r} -> {r['flow']}: {r['reason']}")

    if args.min_accuracy and report["accuracy"] < args.min_accuracy:
        print(f"BELOW GATE: {report['accuracy']}% < {args.min_accuracy}% minimum")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
