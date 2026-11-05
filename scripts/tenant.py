"""Tenant CLI. Registering a client is a data operation, not a code change.

    python scripts/tenant.py list
    python scripts/tenant.py add troogood --vertical ecommerce --display-name TrooGood
    python scripts/tenant.py bind troogood --phone-id 15559227148
    python scripts/tenant.py show troogood
    python scripts/tenant.py layers troogood
    python scripts/tenant.py publish troogood --draft clients/troogood/draft.json --by admin
    python scripts/tenant.py versions troogood
    python scripts/tenant.py rollback troogood --version 1
    python scripts/tenant.py token troogood --label ci
    python scripts/tenant.py smoke leewaysoftech
    python scripts/tenant.py lint troogood
    python scripts/tenant.py reload troogood
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from shared.tenancy import cache, loader, store  # noqa: E402
from shared.tenancy.paths import ensure_client_dir  # noqa: E402

BANNER = "troogood"
SEND2_NUMBER = os.getenv("SEND2_NUMBER", "")


def _default_tenant_id() -> str:
    return BANNER


def cmd_list(args):
    rows = store.list_tenants()
    if not rows:
        print("no tenants registered")
        return
    print(f"{'id':<18} {'vertical':<14} {'phone id':<16} {'ver':<5} status")
    print("-" * 70)
    for r in rows:
        print(
            f"{r['id']:<18} {r['vertical']:<14} {r.get('waba_phone_id') or '-':<16} "
            f"{store.current_version(r['id']):<5} {r['status']}"
        )


def cmd_add(args):
    ensure_client_dir(args.tenant_id)
    store.ensure_tenant(
        args.tenant_id,
        slug=args.tenant_id,
        vertical=args.vertical,
        waba_phone_id=args.phone_id or "",
        display_name=args.display_name or args.tenant_id,
    )
    # Fail fast on an unknown vertical rather than at message time.
    profile = loader.get_tenant_profile(args.tenant_id)
    print(f"registered '{args.tenant_id}' vertical={profile.vertical} "
          f"features_on={sum(1 for v in profile.features.model_dump().values() if v)}")
    if args.bind_number and not args.phone_id:
        ok = store.set_waba_phone_id(args.tenant_id, SEND2_NUMBER)
        print(f"bound SEND2_NUMBER={SEND2_NUMBER}: {ok}")


def cmd_bind(args):
    ok = store.set_waba_phone_id(args.tenant_id, args.phone_id)
    print("bound" if ok else "REFUSED (already bound to another tenant)")


def cmd_show(args):
    profile = loader.get_tenant_profile(args.tenant_id)
    payload = {
        "tenant_id": profile.tenant_id,
        "vertical": profile.vertical,
        "version": profile.version,
        "source": profile.source,
        "display_name": profile.display_name,
        "features_on": sorted(k for k, v in profile.features.model_dump().items() if v),
        "features_off": sorted(k for k, v in profile.features.model_dump().items() if not v),
        "vocabulary": profile.vocabulary.model_dump(),
        "brand": profile.brand.model_dump(),
        "active_intents": profile.active_intent_names(),
        "inactive_intents": sorted(
            set(i.name for i in profile.intents) - set(profile.active_intent_names())
        ),
        "flows": [f.name for f in profile.active_flows()],
        "buttons": [b.id for b in profile.menu.visible_buttons(profile.features)],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_layers(args):
    print(json.dumps(loader.describe_layers(args.tenant_id), indent=2, ensure_ascii=False)[:20000])


def cmd_publish(args):
    if args.draft:
        with open(args.draft, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = store.get_draft(args.tenant_id)
        if payload is None:
            print("no draft on file and no --draft given", file=sys.stderr)
            return 1
    try:
        profile = loader.validate_merged_profile(args.tenant_id, payload)
    except loader.ProfileValidationError as exc:
        print(f"VALIDATION FAILED, nothing published:\n{exc}", file=sys.stderr)
        return 1
    store.save_draft(args.tenant_id, payload, updated_by=args.by)
    version = store.publish_version(args.tenant_id, payload, published_by=args.by)
    cache.purge(args.tenant_id)
    print(f"published {args.tenant_id} v{version} vertical={profile.vertical} "
          f"intents={len(profile.active_intent_names())}")
    return 0


def cmd_versions(args):
    print(json.dumps(store.list_versions(args.tenant_id, limit=args.limit), indent=2))


def cmd_rollback(args):
    if not store.rollback(args.tenant_id, args.version):
        print(f"version {args.version} not found", file=sys.stderr)
        return 1
    cache.purge(args.tenant_id)
    print(f"rolled back to v{args.version}; now live: v{loader.get_tenant_profile(args.tenant_id).version}")
    return 0


def cmd_token(args):
    import hashlib
    import secrets

    raw = secrets.token_urlsafe(40)
    token_id = store.create_tenant_token(
        args.tenant_id,
        hashlib.sha256(raw.encode()).hexdigest(),
        label=args.label,
        created_by="cli",
    )
    print(json.dumps({"id": token_id, "tenant_id": args.tenant_id, "token": raw}, indent=2))
    print("Store this now — only its hash is kept server-side.")


def cmd_smoke(args):
    from shared.tenancy import gating, lint

    profile = loader.get_tenant_profile(args.tenant_id)
    visible = [b.id for b in profile.menu.visible_buttons(profile.features)]
    active = profile.active_intent_names()
    gated = {i: gating.is_enabled(args.tenant_id, f) for i, f in gating.INTENT_GATES.items()}

    # Same scan as lint.lint_forbidden_terms: the guardrails' own keyword lists
    # are vocabularies of banned words, not user-facing copy, and must be
    # excluded or every profile fails on its own forbidden_terms.
    leaks = [
        {"term": h["term"], "in": h["text"]}
        for h in lint.lint_forbidden_terms(profile)
    ]

    report = {
        "tenant_id": profile.tenant_id,
        "vertical": profile.vertical,
        "version": profile.version,
        "buttons": visible,
        "active_intents": active,
        "gated_intents": gated,
        "forbidden_term_leaks": leaks,
        "ok": not leaks,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


def cmd_lint(args):
    """Forbidden-term lint: no commerce wording may survive in a non-commerce profile."""
    from shared.tenancy import lint

    result = lint.lint_tenant(args.tenant_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_reload(args):
    profile = loader.reload_profile(args.tenant_id)
    print(f"reloaded {profile.tenant_id} v{profile.version} source={profile.source}")


def main():
    ap = argparse.ArgumentParser(prog="tenant", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("add")
    p.add_argument("tenant_id", nargs="?", default=_default_tenant_id())
    p.add_argument("--vertical", default="generic")
    p.add_argument("--display-name", default="")
    p.add_argument("--phone-id", default="")
    p.add_argument("--bind-number", action="store_true",
                   help="bind this tenant to SEND2_NUMBER from the environment")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("bind")
    p.add_argument("tenant_id")
    p.add_argument("--phone-id", required=True)
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("show")
    p.add_argument("tenant_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("layers")
    p.add_argument("tenant_id")
    p.set_defaults(func=cmd_layers)

    p = sub.add_parser("publish")
    p.add_argument("tenant_id")
    p.add_argument("--draft", help="JSON file to publish; omit to publish the stored draft")
    p.add_argument("--by", default="cli")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("versions")
    p.add_argument("tenant_id")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("rollback")
    p.add_argument("tenant_id")
    p.add_argument("--version", type=int, required=True)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("token")
    p.add_argument("tenant_id")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("smoke")
    p.add_argument("tenant_id")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("lint")
    p.add_argument("tenant_id")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("reload")
    p.add_argument("tenant_id")
    p.set_defaults(func=cmd_reload)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
