"""Prompt config comes from the tenant profile, with the legacy dict as fallback."""
import sys

# Repo root only. "backend" must NOT precede it: there are two packages named
# routing, and the live one (the only one with prompts.py) is the root copy.
sys.path.insert(0, ".")

from routing.prompts import (  # noqa: E402
    COMPANY_TYPE_CONFIGS,
    get_company_config,
    get_listing_prompt,
    get_profile_prompt_config,
    get_system_prompt,
)

print("--- profile-backed config (tenant id as company_type) ---")
for tenant in ("troogood",):
    cfg = get_company_config(tenant)
    print(f"  {tenant:<12} name={cfg['name']!r} listing={cfg['listing_type']!r}")
    assert "cart" in cfg["keywords"], "ecommerce keywords must survive the migration"

print("\n--- vertical profiles via the CLI-registered tenant ---")
for vertical, tenant in (("it_software", "leewaysoftech"),):
    from shared.tenancy import loader

    p = loader.build_profile(tenant, db_layer={"vertical": vertical}, file_layer={},
                             include_db=False)
    cfg = p.prompt.model_dump()
    assert "software" in cfg["keywords"]
    assert not any(k == "cart" for k in cfg["keywords"])
    print(f"  {vertical:<14} name={cfg['name']!r}")
    print(f"  {'':<14} listing={cfg['listing_type']!r}")
    print(f"  {'':<14} keywords={cfg['keywords'][:6]}...")

print("\n--- legacy fallbacks still work (unmigrated call sites) ---")
for key in ("it", "ecommerce", "food", "manufacturing", "healthcare", "retail", "generic"):
    cfg = get_company_config(key)
    assert cfg is COMPANY_TYPE_CONFIGS[key], key
    print(f"  {key:<14} -> {cfg['name']!r} (legacy dict, unchanged)")

print("\n--- unknown tenant falls back to generic, does not raise ---")
cfg = get_company_config("does-not-exist")
print("  ", cfg["name"])

print("\n--- system prompt builds from profile config ---")
prompt = get_system_prompt("troogood")
assert "E-commerce" in prompt or "retail" in prompt, prompt[:400]
print(f"  troogood prompt: {len(prompt)} chars, mentions retail: {'retail' in prompt}")

print("\n--- get_profile_prompt_config returns None for a missing profile ---")
print("  ", get_profile_prompt_config("no-such-tenant-xyz"))

print("\n--- listing prompt ---")
print("  ", get_listing_prompt("troogood").splitlines()[0][:100])

print("\nOK")
