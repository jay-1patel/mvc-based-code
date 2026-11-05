"""Local verification for the tenant-scoped offerings API.

Mints a session token for the existing super admin (no password involved) and
exercises create / list / update / delete across two tenants, so the isolation
claim is checked rather than assumed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath("backend"))
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient  # noqa: E402

from database import get_db_context  # noqa: E402
from main import app  # noqa: E402
from routes.auth import _create_token, hash_tenant_token  # noqa: E402
from shared.tenancy import store as tenancy_store  # noqa: E402

with get_db_context() as conn:
    owners = [r[0] for r in conn.execute("SELECT id FROM tenants ORDER BY id").fetchall()]

a, b = owners[0], owners[1]
admin_token = _create_token("superadmin")
raw_tenant_token = "verify-" + os.urandom(8).hex()
token_id = tenancy_store.create_tenant_token(b, hash_tenant_token(raw_tenant_token), "verify-script")
tenant_token = raw_tenant_token

client = TestClient(app)
H = {"Authorization": f"Bearer {admin_token}"}
failures = []


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ((" :: " + str(extra)) if extra else ""))
    if not cond:
        failures.append(label)


# 1. unauthenticated
r = client.get(f"/api/admin/tenants/{a}/offerings")
check("unauthenticated read is 401", r.status_code == 401, r.status_code)

# 2. super admin creates a service for tenant A
r = client.post(
    f"/api/admin/tenants/{a}/offerings",
    headers=H,
    json={
        "name": "Verification Web App",
        "category": "Web development",
        "short_description": "React and Node.js build",
        "price": "₹2,00,000",
        "attrs": {"tech_stack": "React, Node.js", "timeline": "8 weeks"},
    },
)
check("create returns 200", r.status_code == 200, r.text[:200])
created = r.json().get("offering", {})
cid = created.get("id")

# 3. slug is unique across tenants, so tenant B gets a suffixed slug
r = client.post(f"/api/admin/tenants/{b}/offerings", headers=H,
                json={"name": "Verification Web App", "category": "Web development"})
other = r.json().get("offering", {})
check("cross-tenant slug does not collide",
      other.get("slug") not in (None, created.get("slug")), (created.get("slug"), other.get("slug")))

# 4. each tenant only sees its own rows
la = client.get(f"/api/admin/tenants/{a}/offerings", headers=H).json()["offerings"]
lb = client.get(f"/api/admin/tenants/{b}/offerings", headers=H).json()["offerings"]
ids_a = {o["id"] for o in la}
ids_b = {o["id"] for o in lb}
check("tenant A sees its own row", cid in ids_a)
check("tenant B does not see tenant A's row", cid not in ids_b)
check("tenant B sees only its own row", {o["id"] for o in lb} == {other.get("id")})

# 5. cross-tenant write by id is refused (404, not a silent no-op)
r = client.put(f"/api/admin/tenants/{b}/offerings/{cid}", headers=H, json={"name": "hijacked"})
check("cross-tenant update is 404", r.status_code == 404, r.status_code)
r = client.delete(f"/api/admin/tenants/{b}/offerings/{cid}", headers=H)
check("cross-tenant delete is 404", r.status_code == 404, r.status_code)

# 6. update + deactivate + restore
r = client.put(f"/api/admin/tenants/{a}/offerings/{cid}", headers=H,
               json={"short_description": "Edited", "is_active": False})
check("update returns 200", r.status_code == 200, r.text[:200])
check("update persisted", r.json()["offering"]["short_description"] == "Edited")
la = client.get(f"/api/admin/tenants/{a}/offerings", headers=H).json()["offerings"]
check("deactivated row leaves the default list", cid not in {o["id"] for o in la})
la = client.get(f"/api/admin/tenants/{a}/offerings?include_inactive=true", headers=H).json()["offerings"]
check("deactivated row is returned when asked for", cid in {o["id"] for o in la})

# 7. a tenant token only reaches its own tenant
r = client.get(f"/api/admin/tenants/{b}/offerings",
               headers={"Authorization": f"Bearer {tenant_token}"})
check("tenant token reads its own tenant", r.status_code == 200, r.status_code)
r = client.get(f"/api/admin/tenants/{a}/offerings",
               headers={"Authorization": f"Bearer {tenant_token}"})
check("tenant token is refused for another tenant", r.status_code == 403, r.status_code)

# 8. categories
r = client.get(f"/api/admin/tenants/{a}/offerings/categories", headers=H)
check("categories returns 200", r.status_code == 200, r.text[:200])

# 9. a sub admin without manage_operations is refused
sub = _create_token("admin2")
r = client.get(f"/api/admin/tenants/{a}/offerings", headers={"Authorization": f"Bearer {sub}"})
check("sub admin without manage_operations is 403", r.status_code == 403, r.status_code)

# cleanup
client.delete(f"/api/admin/tenants/{a}/offerings/{cid}?hard=true", headers=H)
client.delete(f"/api/admin/tenants/{b}/offerings/{other.get('id')}?hard=true", headers=H)
tenancy_store.revoke_tenant_token(token_id)

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
