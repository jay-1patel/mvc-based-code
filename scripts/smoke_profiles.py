"""Smoke check for the tenancy substrate."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from shared.tenancy import lint, loader  # noqa: E402

FAILURES = []

for vertical in ("ecommerce", "it_software", "tours_travel", "banking", "finance", "generic"):
    p = loader.build_profile(
        "smoke", db_layer={"vertical": vertical}, file_layer={}, include_db=False
    )
    print(f"\n=== {p.vertical} ({p.display_name}) ===")
    print("  features on  :", sorted(k for k, v in p.features.model_dump().items() if v))
    print("  active intents:", len(p.active_intent_names()), "of", len(p.intents))
    print("  hidden intents:", sorted(
        set(i.name for i in p.intents) - set(p.active_intent_names())))
    print("  flows        :", [f.name for f in p.active_flows()])
    print("  buttons      :", [b.id for b in p.menu.visible_buttons(p.features)])
    print("  browse_label :", p.v("browse_label"))

print("\n--- per-item override keeps sibling defaults ---")
p = loader.build_profile(
    "smoke",
    db_layer={"vertical": "it_software", "flows": [
        {"name": "get_quote", "success_message": "Leeway will call you within one business day."}]},
    file_layer={},
    include_db=False,
)
print("  flow count      :", len(p.flows), "(defaults kept)")
print("  get_quote msg   :", p.flow("get_quote").success_message)
print("  get_quote steps :", len(p.flow("get_quote").steps), "(steps kept)")

print("\n--- __remove__ drops a default intent ---")
p = loader.build_profile(
    "smoke",
    db_layer={"vertical": "ecommerce", "intents": [{"name": "place_order", "__remove__": True}]},
    file_layer={},
    include_db=False,
)
print("  place_order present:", "place_order" in p.active_intent_names())
print("  order_status present:", "order_status" in p.active_intent_names())

print("\n--- menu button removal ---")
p = loader.build_profile(
    "smoke",
    db_layer={"vertical": "ecommerce", "menu": {"buttons": [
        {"id": "menu_view_cart", "__remove__": True}]}},
    file_layer={},
    include_db=False,
)
print("  buttons:", [b.id for b in p.menu.visible_buttons(p.features)])

print("\n--- flow graph is wired (no dead ends) ---")
for vertical in ("ecommerce", "it_software", "tours_travel", "banking", "finance", "generic"):
    p = loader.build_profile(
        "smoke", db_layer={"vertical": vertical}, file_layer={}, include_db=False)
    for flow in p.flows:
        # Every step except the last must have a hop out; the last ends the flow.
        for step in flow.steps[:-1]:
            if not (step.on or step.next or step.goto):
                FAILURES.append(
                    f"{vertical}/{flow.name}: step '{step.id}' has no outgoing hop")
        last = flow.steps[-1]
        if last.on or last.next or last.goto:
            FAILURES.append(
                f"{vertical}/{flow.name}: last step '{last.id}' should end the flow")
    print(f"  {vertical:<14} {len(p.flows)} flows, every step hands off or ends the flow")

print("\n--- profile lint (all verticals) ---")
for vertical in ("ecommerce", "it_software", "tours_travel", "banking", "finance", "generic"):
    p = loader.build_profile(
        "smoke", db_layer={"vertical": vertical}, file_layer={}, include_db=False)
    r = lint.lint_profile(p)
    problems = r["forbidden_terms"] + r["intent_gating"] + r["flow_reachability"]
    if problems:
        FAILURES.extend(f"{vertical}: {prob}" for prob in problems)
    print(f"  {vertical:<14} ok={r['ok']}  "
          f"forbidden={len(r['forbidden_terms'])} "
          f"gating={len(r['intent_gating'])} "
          f"reachability={len(r['flow_reachability'])}")

if FAILURES:
    print("\nFAILURES:")
    for f in FAILURES:
        print("  -", f)
    print("\nFAILED")
    sys.exit(1)

print("\nOK")

