import sys
import sqlite3

sys.path.insert(0, "backend")
conn = sqlite3.connect("faq.db")
conn.row_factory = sqlite3.Row

for table in ("tenants", "tenant_profile_versions", "tenant_tokens", "leads", "handoffs"):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    print(table, "->", cols)

print("products cols:", [r[1] for r in conn.execute("PRAGMA table_info(products)")])
print("user_states cols:", [r[1] for r in conn.execute("PRAGMA table_info(user_states)")])
print("cached_embeddings cols:", [r[1] for r in conn.execute("PRAGMA table_info(cached_embeddings)")])
print("faq_dataset cols:", [r[1] for r in conn.execute("PRAGMA table_info(faq_dataset)")])
print("products count:", conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
print("blank prices now:", conn.execute(
    "SELECT COUNT(*) FROM products WHERE TRIM(COALESCE(price, '')) = ''").fetchone()[0])
print("null price now:", conn.execute(
    "SELECT COUNT(*) FROM products WHERE price IS NULL").fetchone()[0])

# Unbound tenants must be able to coexist: the uniqueness on waba_phone_id is a
# partial index, not a column constraint, and blank is stored as NULL.
print("\ntenants indexes:", [r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tenants'")])
print("tenants rows:", [dict(r) for r in conn.execute(
    "SELECT id, waba_phone_id, vertical FROM tenants ORDER BY id")])
blank = conn.execute(
    "SELECT COUNT(*) FROM tenants WHERE waba_phone_id = ''").fetchone()[0]
print("tenants with blank phone id:", blank, "(want 0 - NULL instead)")
print("unbound tenants:", conn.execute(
    "SELECT COUNT(*) FROM tenants WHERE waba_phone_id IS NULL OR waba_phone_id = ''"
).fetchone()[0])

# Every content row must be owned by a tenant that actually exists, otherwise a
# tenant-scoped search silently returns nothing.
print("\ncontent tenant_id distribution:")
for table in ("faq_dataset", "cached_embeddings", "knowledge_base", "products"):
    rows = [dict(r) for r in conn.execute(
        f"SELECT tenant_id, COUNT(*) c FROM {table} GROUP BY tenant_id")]
    known = [r for r in rows if r["tenant_id"] in ("troogood",) or r["tenant_id"]]
    print(f"  {table:<20} {rows}")
orphans = conn.execute(
    "SELECT COUNT(*) FROM faq_dataset WHERE tenant_id NOT IN (SELECT id FROM tenants)"
).fetchone()[0]
print("faq rows owned by a non-existent tenant:", orphans, "(want 0)")
conn.close()
