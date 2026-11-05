"""SQLite access for tenants + tenant_profile_versions.

All JSON lives in TEXT columns (decision D2) — no jsonb. Postgres is a future
migration, not now.

Publish model:
  * the *working copy* an admin edits lives in the existing ``draft_config``
    table under scope ``tenant_profile:<tenant_id>`` — same draft/publish
    machinery every other dynamic-config scope already uses.
  * publishing validates the fully-merged profile, appends a numbered row to
    ``tenant_profile_versions``, and flips ``is_current``. Old versions are kept.
  * rollback re-points ``is_current`` at an earlier version. Nothing is deleted.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .paths import BACKEND_DIR
import sys as _sys

logger = logging.getLogger("tenancy.store")

if str(BACKEND_DIR) not in _sys.path:
    _sys.path.insert(0, str(BACKEND_DIR))

PROFILE_SCOPE_PREFIX = "tenant_profile:"
VERSION_TTL_SECONDS = 60.0

_version_lock = threading.RLock()
_version_cache: Dict[str, Tuple[float, int]] = {}


def _db():
    import database  # backend/database.py — the sole schema owner
    return database


def profile_scope(tenant_id: str) -> str:
    return f"{PROFILE_SCOPE_PREFIX}{tenant_id}"


def tenant_id_from_scope(scope: str) -> Optional[str]:
    if scope and scope.startswith(PROFILE_SCOPE_PREFIX):
        return scope[len(PROFILE_SCOPE_PREFIX):]
    return None


# ── tenants ───────────────────────────────────────────────────────────────

def ensure_tenant(tenant_id: str, *, slug: str = "", vertical: str = "generic",
                  waba_phone_id: str = "", mobile_number: str = "",
                  display_name: str = "",
                  status: str = "active", conn=None) -> bool:
    """Idempotent upsert. Never clobbers an existing waba_phone_id with blank.

    An unbound tenant stores NULL rather than '' so the partial unique index on
    waba_phone_id excludes it. ``mobile_number`` is the required business mobile
    (digits); blanks are not written on update.
    """
    def _write(connection):
        # Look for existing tenant by ID, slug, or normalized slug
        row = connection.execute(
            "SELECT id FROM tenants WHERE id = ? OR slug = ? OR lower(slug) = lower(?)",
            (tenant_id, slug or tenant_id, (slug or tenant_id).lower()),
        ).fetchone()

        if row:
            connection.execute(
                """UPDATE tenants SET
                       slug = CASE WHEN ? != '' THEN ? ELSE slug END,
                       waba_phone_id = CASE WHEN ? != '' THEN ? ELSE waba_phone_id END,
                       mobile_number = CASE WHEN ? != '' THEN ? ELSE mobile_number END,
                       vertical = ?,
                       status = ?,
                       display_name = CASE WHEN ? != '' THEN ? ELSE display_name END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    slug or tenant_id, slug or tenant_id,
                    waba_phone_id or '', waba_phone_id or None,
                    mobile_number or '', mobile_number or None,
                    vertical, status,
                    display_name, display_name,
                    row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0],
                ),
            )
        else:
            # Check if id column is INTEGER autoincrement
            col_type = "TEXT"
            try:
                for c in connection.execute("PRAGMA table_info(tenants)").fetchall():
                    if (c["name"] if hasattr(c, "keys") else c[1]) == "id":
                        col_type = (c["type"] if hasattr(c, "keys") else c[2] or "").upper()
            except Exception:
                # deliberate: the id-type probe is best-effort — TEXT is the
                # safe default.
                logger.debug("TENANT_ID_TYPE_PROBE_SKIPPED | defaulting to TEXT", exc_info=True)

            if "INT" in col_type and not str(tenant_id).isdigit():
                connection.execute(
                    """INSERT INTO tenants (company_name, slug, waba_phone_id, mobile_number, vertical, status, display_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (display_name or slug or tenant_id, slug or tenant_id, waba_phone_id or None,
                     mobile_number or None, vertical, status, display_name),
                )
            else:
                connection.execute(
                    """INSERT INTO tenants (id, slug, waba_phone_id, mobile_number, vertical, status, display_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tenant_id, slug or tenant_id, waba_phone_id or None,
                     mobile_number or None, vertical, status, display_name),
                )

    if conn is None:
        with _db().get_db_context() as own_conn:
            _write(own_conn)
    else:
        _write(conn)
    invalidate(tenant_id)
    return True


def _normalize_tenant_key(value: str) -> str:
    """Collapse id/slug punctuation so ``leeway-softech`` == ``leewaysoftech``."""
    return str(value or "").replace("-", "").replace("_", "").replace(" ", "").lower()


def find_registry_conflict(tenant_id: str, slug: str = "") -> Optional[dict]:
    """Return a tenant that already owns this registry id/slug.

    Used by create-tenant / approval flows. Matches id and slug only
    (exact + case-insensitive + punctuation-normalized). Does **not** match
    ``display_name`` / ``company_name`` — those aliases are for inbound
    resolution via ``get_tenant``, and treating them as id conflicts falsely
    blocks registering ``leeway-softech`` when another row is merely *named*
    "Leeway Softech".
    """
    tid = str(tenant_id or "").strip()
    slug_v = str(slug or tid).strip() or tid
    if not tid:
        return None
    norms = {_normalize_tenant_key(tid), _normalize_tenant_key(slug_v)}
    norms.discard("")
    if not norms:
        return None
    placeholders = ", ".join("?" for _ in norms)
    norm_params = tuple(norms)
    with _db().get_db_context() as conn:
        try:
            row = conn.execute(
                f"""SELECT * FROM tenants WHERE
                       id = ? OR id = ?
                       OR LOWER(COALESCE(slug, '')) = LOWER(?)
                       OR LOWER(COALESCE(slug, '')) = LOWER(?)
                       OR LOWER(REPLACE(REPLACE(REPLACE(COALESCE(id, ''), '-', ''), '_', ''), ' ', ''))
                            IN ({placeholders})
                       OR LOWER(REPLACE(REPLACE(REPLACE(COALESCE(slug, ''), '-', ''), '_', ''), ' ', ''))
                            IN ({placeholders})
                    LIMIT 1""",
                (tid, slug_v, tid, slug_v) + norm_params + norm_params,
            ).fetchone()
        except Exception as exc:
            logger.warning(
                "Could not check tenant registry conflict for %s: %s", tenant_id, exc
            )
            return None
    return dict(row) if row else None


def get_tenant(tenant_id: str) -> Optional[dict]:
    with _db().get_db_context() as conn:
        try:
            tid_str = str(tenant_id or "").strip()
            norm = _normalize_tenant_key(tid_str)
            cols = {c[1] for c in conn.execute("PRAGMA table_info(tenants)").fetchall()}
            # Resolve by id/slug (exact + normalized). Also accept display_name /
            # company_name aliases for inbound routing — e.g. a user types the
            # brand name. Prefix LIKE ("l%" → leeway-softech / ltfinance) is
            # intentionally avoided; it caused cross-tenant profile loads.
            # Create/register must NOT use this helper — see find_registry_conflict.
            where_clauses = [
                "id = ?",
                "slug = ?",
                "LOWER(slug) = LOWER(?)",
                "LOWER(REPLACE(REPLACE(REPLACE(slug, '-', ''), '_', ''), ' ', '')) = ?",
            ]
            params = [tid_str, tid_str, tid_str, norm]
            if "company_name" in cols:
                where_clauses.append(
                    "LOWER(REPLACE(REPLACE(REPLACE(company_name, ' ', ''), '-', ''), '_', '')) = ?"
                )
                params.append(norm)
            if "display_name" in cols:
                where_clauses.append(
                    "LOWER(REPLACE(REPLACE(REPLACE(display_name, ' ', ''), '-', ''), '_', '')) = ?"
                )
                params.append(norm)

            row = conn.execute(
                f"SELECT * FROM tenants WHERE {' OR '.join(where_clauses)}",
                tuple(params),
            ).fetchone()
        except Exception as exc:
            # Tolerate a database that has not been migrated yet — the loader
            # falls back to file + vertical defaults rather than hard-failing.
            logger.warning("Could not read tenant %s (schema not migrated?): %s", tenant_id, exc)
            return None
    return dict(row) if row else None


def list_tenants(status: Optional[str] = None) -> List[dict]:
    with _db().get_db_context() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tenants WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tenants ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def normalize_waba_phone_id(value: str) -> str:
    """Normalize a WhatsApp phone binding to digits only.

    Accepts either the Meta phone-number id (all digits) or the WhatsApp
    number itself in ``+91 8000305305`` form. Inbound webhook payloads
    carry the number without the plus sign (``metadata.display_phone_number``),
    so digits-only is the form the resolver matches against.
    """
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 16:
        raise ValueError(
            "waba_phone_id must be the WhatsApp phone number id or the "
            "number itself in international format (10-16 digits)"
        )
    return digits


def normalize_mobile_number(value: str) -> str:
    """Normalize a tenant business mobile to digits (country code + number)."""
    raw = str(value or "").strip()
    if raw.upper().startswith("UNSET-"):
        raise ValueError("mobile_number placeholder is not a real number")
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        raise ValueError(
            "mobile_number must be an international mobile number (10-15 digits)"
        )
    return digits


def find_tenant_by_mobile(mobile_number: str) -> Optional[str]:
    try:
        digits = normalize_mobile_number(mobile_number)
    except ValueError:
        digits = re.sub(r"\D", "", str(mobile_number or ""))
    if not digits:
        return None
    with _db().get_db_context() as conn:
        try:
            row = conn.execute(
                "SELECT id FROM tenants WHERE mobile_number = ?", (digits,)
            ).fetchone()
        except Exception:
            return None
    return row["id"] if row else None


def set_mobile_number(tenant_id: str, mobile_number: str, conn=None) -> bool:
    """Set the tenant's required business mobile. Refuses duplicates."""
    digits = normalize_mobile_number(mobile_number)

    def _bind(connection):
        clash = connection.execute(
            "SELECT id FROM tenants WHERE mobile_number = ? AND id != ?",
            (digits, tenant_id),
        ).fetchone()
        if clash:
            logger.warning(
                "mobile_number %s already bound to tenant %s; refusing %s",
                digits, clash["id"], tenant_id,
            )
            return False
        connection.execute(
            "UPDATE tenants SET mobile_number = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (digits, str(tenant_id)),
        )
        return True

    if conn is None:
        with _db().get_db_context() as own_conn:
            ok = _bind(own_conn)
    else:
        ok = _bind(conn)
    if ok:
        invalidate(tenant_id)
    return ok


def set_waba_phone_id(tenant_id: str, phone_id: str, conn=None) -> bool:
    """Bind a WABA phone-id to a tenant. Refuses to steal an existing binding."""
    phone_id = str(phone_id or "").strip()
    if not phone_id:
        return False
    def _bind(connection):
        clash = connection.execute(
            "SELECT id FROM tenants WHERE waba_phone_id = ? AND id != ?", (phone_id, tenant_id)
        ).fetchone()
        if clash:
            logger.warning(
                "waba_phone_id %s already bound to tenant %s; refusing to rebind to %s",
                phone_id, clash["id"], tenant_id,
            )
            return False
        connection.execute(
            "UPDATE tenants SET waba_phone_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (phone_id, str(tenant_id)),
        )
        return True

    if conn is None:
        with _db().get_db_context() as own_conn:
            ok = _bind(own_conn)
    else:
        ok = _bind(conn)
    if ok:
        invalidate(tenant_id)
    return ok


def find_tenant_by_phone_id(phone_id: str) -> Optional[str]:
    phone_id = str(phone_id or "").strip()
    if not phone_id:
        return None
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT id FROM tenants WHERE waba_phone_id = ?", (phone_id,)
        ).fetchone()
    return row["id"] if row else None


def tenant_id_for_user(wa_id: str) -> Optional[str]:
    """Which tenant a WhatsApp user belongs to, from their conversation state.

    user_states is keyed by wa_id and already carries tenant_id, so this is one
    indexed read. Returns None for a user with no state row.
    """
    wa_id = str(wa_id or "").strip()
    if not wa_id:
        return None
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT tenant_id FROM user_states WHERE wa_id = ?", (wa_id,)
        ).fetchone()
    if not row or row["tenant_id"] is None:
        return None
    tid = str(row["tenant_id"]).strip()
    return tid or None


# Tables that keep rows after a tenant is wiped (compliance / ops history).
_PURGE_SKIP_TABLES = frozenset({
    "admin_audit_events",
    "sqlite_sequence",
    "sqlite_stat1",
})


def _tables_with_column(connection, column: str) -> List[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    out: List[str] = []
    for row in rows:
        name = row["name"] if hasattr(row, "keys") else row[0]
        if name in _PURGE_SKIP_TABLES:
            continue
        cols = [
            (c["name"] if hasattr(c, "keys") else c[1])
            for c in connection.execute(f"PRAGMA table_info({name})").fetchall()
        ]
        if column in cols:
            out.append(str(name))
    return sorted(out)


def _purge_tenant_disk(tenant_id: str, slug: str = "") -> List[str]:
    """Remove clients/<id|slug> packs under repo and backend roots."""
    import shutil
    from .paths import BACKEND_CLIENTS_DIR, CLIENTS_DIR

    removed: List[str] = []
    names = {str(tenant_id).strip()}
    if slug and str(slug).strip():
        names.add(str(slug).strip())
    roots = [CLIENTS_DIR, BACKEND_CLIENTS_DIR]
    for root in roots:
        try:
            if not root.exists():
                continue
        except Exception:
            continue
        for name in names:
            if not name or name in (".", ".."):
                continue
            path = root / name
            try:
                if path.is_dir() and path.resolve().parent == root.resolve():
                    shutil.rmtree(path)
                    removed.append(str(path))
            except Exception as exc:
                logger.warning("TENANT_PURGE_DISK_FAILED | path=%s | %s", path, exc)
    return removed


def purge_tenant_data(tenant_id: str, conn=None, *, slug: str = "") -> Dict[str, Any]:
    """Permanently delete all DB rows and client-pack files for a tenant.

    Keeps ``admin_audit_events`` so who-deleted-what remains visible.
    """
    tid = str(tenant_id or "").strip()
    if not tid:
        raise ValueError("tenant_id is required")

    summary: Dict[str, Any] = {"tenant_id": tid, "tables": {}, "disk": []}

    def _purge(connection):
        # Child event tables keyed by request id (no tenant_id column).
        try:
            connection.execute(
                """DELETE FROM api_onboarding_request_events WHERE request_id IN (
                     SELECT id FROM api_onboarding_requests WHERE tenant_id = ?
                   )""",
                (tid,),
            )
        except Exception:
            # deliberate: purge is per-table best-effort — a missing child
            # table must not stop the tenant purge.
            logger.debug("TENANT_PURGE_TABLE_SKIPPED | table=api_onboarding_request_events tid=%s", tid, exc_info=True)
        try:
            connection.execute(
                """DELETE FROM tenant_change_request_events WHERE request_id IN (
                     SELECT id FROM tenant_change_requests WHERE tenant_id = ?
                   )""",
                (tid,),
            )
        except Exception:
            # deliberate: purge is per-table best-effort — a missing child
            # table must not stop the tenant purge.
            logger.debug("TENANT_PURGE_TABLE_SKIPPED | table=tenant_change_request_events tid=%s", tid, exc_info=True)

        scope = profile_scope(tid)
        for table, sql, params in (
            ("draft_config", "DELETE FROM draft_config WHERE scope = ?", (scope,)),
            ("published_config", "DELETE FROM published_config WHERE scope = ?", (scope,)),
            ("publish_history", "DELETE FROM publish_history WHERE scope = ?", (scope,)),
        ):
            try:
                cur = connection.execute(sql, params)
                summary["tables"][table] = int(cur.rowcount or 0)
            except Exception as exc:
                logger.warning("TENANT_PURGE_TABLE_FAILED | table=%s | %s", table, exc)

        for table in _tables_with_column(connection, "tenant_id"):
            if table == "tenants":
                continue
            try:
                cur = connection.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ?", (tid,)
                )
                summary["tables"][table] = int(cur.rowcount or 0)
            except Exception as exc:
                logger.warning("TENANT_PURGE_TABLE_FAILED | table=%s | %s", table, exc)

        # Clear WhatsApp test override if it pointed at this tenant.
        try:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                ("active_test_tenant",),
            ).fetchone()
            active = ""
            if row:
                active = str(row["value"] if hasattr(row, "keys") else row[0] or "").strip()
            if active and active in {tid, str(slug or "").strip()}:
                connection.execute(
                    "DELETE FROM app_settings WHERE key = ?",
                    ("active_test_tenant",),
                )
                summary["tables"]["app_settings.active_test_tenant"] = 1
        except Exception:
            # deliberate: purge is per-table best-effort.
            logger.debug("TENANT_PURGE_TABLE_SKIPPED | table=app_settings tid=%s", tid, exc_info=True)

        cur = connection.execute("DELETE FROM tenants WHERE id = ?", (tid,))
        summary["tables"]["tenants"] = int(cur.rowcount or 0)

    if conn is None:
        with _db().get_db_context() as own_conn:
            _purge(own_conn)
    else:
        _purge(conn)

    summary["disk"] = _purge_tenant_disk(tid, slug=slug)
    invalidate(tid)
    logger.info(
        "TENANT_PURGED | tenant=%s | tables=%s | disk=%s",
        tid,
        {k: v for k, v in summary["tables"].items() if v},
        summary["disk"],
    )
    return summary


def delete_tenant(tenant_id: str, conn=None) -> bool:
    """Hard-delete a tenant registry row and all of its tenant-scoped data."""
    record = None
    try:
        record = get_tenant(tenant_id)
    except Exception:
        record = None
    slug = str((record or {}).get("slug") or "").strip()
    purge_tenant_data(tenant_id, conn=conn, slug=slug)
    return True


# ── version bookkeeping ───────────────────────────────────────────────────

def current_version(tenant_id: str) -> int:
    """Current published version for a tenant. 0 means 'no DB version'."""
    tid = str(tenant_id)
    now = time.monotonic()
    with _version_lock:
        hit = _version_cache.get(tid)
        if hit and hit[0] >= now:
            return hit[1]
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT version FROM tenant_profile_versions "
            "WHERE tenant_id = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
            (tid,),
        ).fetchone()
    version = int(row["version"]) if row else 0
    with _version_lock:
        _version_cache[tid] = (now + VERSION_TTL_SECONDS, version)
    return version


def invalidate(tenant_id: str) -> None:
    with _version_lock:
        _version_cache.pop(str(tenant_id), None)


def invalidate_all() -> None:
    with _version_lock:
        _version_cache.clear()


def get_current_payload(tenant_id: str) -> Optional[dict]:
    """The DB override layer: the current published version's payload."""
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT payload FROM tenant_profile_versions "
            "WHERE tenant_id = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
            (str(tenant_id),),
        ).fetchone()
    if not row or not row["payload"]:
        return None
    try:
        return json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("tenant %s has unparseable current payload: %s", tenant_id, exc)
        return None


def get_version(tenant_id: str, version: int) -> Optional[dict]:
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_profile_versions WHERE tenant_id = ? AND version = ?",
            (str(tenant_id), int(version)),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["payload"] = json.loads(out["payload"]) if out["payload"] else {}
    except (json.JSONDecodeError, TypeError):
        out["payload"] = {}
    out["is_current"] = bool(out.get("is_current"))
    return out


def list_versions(tenant_id: str, limit: int = 50) -> List[dict]:
    with _db().get_db_context() as conn:
        rows = conn.execute(
            """SELECT id, tenant_id, version, published_by, created_at, is_current
                 FROM tenant_profile_versions
                WHERE tenant_id = ?
                ORDER BY version DESC LIMIT ?""",
            (str(tenant_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def _set_current(conn, tenant_id: str, version: int) -> None:
    conn.execute(
        "UPDATE tenant_profile_versions SET is_current = 0 WHERE tenant_id = ? AND is_current = 1",
        (str(tenant_id),),
    )
    conn.execute(
        "UPDATE tenant_profile_versions SET is_current = 1 WHERE tenant_id = ? AND version = ?",
        (str(tenant_id), int(version)),
    )


def publish_version(tenant_id: str, payload: dict, published_by: str = "", conn=None, validate: bool = True) -> int:
    """Append a new version and make it current. Returns the new version number.

    Validates the payload through the loader (vertical defaults + draft merged
    into a TenantProfile) before anything is written, so no caller can publish
    a profile the bot would reject on load — a bad website/phone string used to
    become the live version and the loader fell back to a skeleton profile.
    Routes that pre-validate are unaffected (double validation is cheap and
    read-only). Pass validate=False only for trusted repair scripts.
    """
    tid = str(tenant_id)
    if validate:
        from shared.tenancy import loader as _loader  # lazy: loader imports store
        _loader.validate_merged_profile(tid, payload)
    body = json.dumps(payload, ensure_ascii=False)
    def _publish(connection):
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM tenant_profile_versions WHERE tenant_id = ?",
            (tid,),
        ).fetchone()
        next_version = int(row["v"]) + 1
        # Insert with is_current = 0: the partial unique index
        # ux_tenant_profile_current only allows ONE current row per tenant, so
        # _set_current must clear the previous one first. Inserting as current
        # straight away violates the index on every publish after the first.
        connection.execute(
            """INSERT INTO tenant_profile_versions
                   (tenant_id, version, payload, published_by, created_at, is_current)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 0)""",
            (tid, next_version, body, published_by or ""),
        )
        _set_current(connection, tid, next_version)
        # Mirror into published_config so the generic config surfaces stay consistent.
        connection.execute(
            """INSERT INTO published_config (scope, snapshot_json, published_by, published_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(scope) DO UPDATE SET
                   snapshot_json = excluded.snapshot_json,
                   published_by = excluded.published_by,
                   published_at = CURRENT_TIMESTAMP""",
            (profile_scope(tid), body, published_by or ""),
        )
        connection.execute(
            "INSERT INTO publish_history (scope, snapshot_json, published_by, published_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (profile_scope(tid), body, published_by or ""),
        )
        return next_version

    if conn is None:
        with _db().get_db_context() as own_conn:
            next_version = _publish(own_conn)
    else:
        next_version = _publish(conn)
    invalidate(tid)
    return next_version


def rollback(tenant_id: str, version: int, conn=None) -> bool:
    """Re-point is_current at an earlier version. Nothing is deleted."""
    tid = str(tenant_id)
    def _rollback(connection):
        row = connection.execute(
            "SELECT payload FROM tenant_profile_versions WHERE tenant_id = ? AND version = ?",
            (tid, int(version)),
        ).fetchone()
        if not row:
            return False
        _set_current(connection, tid, int(version))
        connection.execute(
            """INSERT INTO published_config (scope, snapshot_json, published_by, published_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(scope) DO UPDATE SET
                   snapshot_json = excluded.snapshot_json,
                   published_by = excluded.published_by,
                   published_at = CURRENT_TIMESTAMP""",
            (profile_scope(tid), row["payload"], "rollback"),
        )
        connection.execute(
            "INSERT INTO publish_history (scope, snapshot_json, published_by, published_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (profile_scope(tid), row["payload"], "rollback"),
        )
        return True

    if conn is None:
        with _db().get_db_context() as own_conn:
            ok = _rollback(own_conn)
    else:
        ok = _rollback(conn)
    if ok:
        invalidate(tid)
    return ok


# ── draft (working copy) ──────────────────────────────────────────────────

# A working copy nobody publishes within this window is stale by design:
# reading it after the lease expires discards it (see get_draft_config).
DRAFT_TTL_DAYS = 7.0


def get_draft(tenant_id: str) -> Optional[dict]:
    return _db().get_draft_config(
        profile_scope(tenant_id), default=None, max_age_days=DRAFT_TTL_DAYS
    )


def save_draft(tenant_id: str, payload: dict, updated_by: str = "", conn=None) -> bool:
    return _db().save_draft_config(profile_scope(tenant_id), payload, updated_by=updated_by, conn=conn)


def get_published_mirror(tenant_id: str) -> Optional[dict]:
    return _db().get_published_config(profile_scope(tenant_id), default=None)


# ── tokens ────────────────────────────────────────────────────────────────

def list_tenant_tokens(tenant_id: str) -> List[dict]:
    with _db().get_db_context() as conn:
        rows = conn.execute(
            "SELECT id, tenant_id, label, created_by, created_at, revoked_at "
            "FROM tenant_tokens WHERE tenant_id = ? ORDER BY id DESC",
            (str(tenant_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tenant_token(token_hash: str) -> Optional[dict]:
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_tokens WHERE token_hash = ? AND revoked_at IS NULL",
            (str(token_hash),),
        ).fetchone()
    return dict(row) if row else None


def create_tenant_token(tenant_id: str, token_hash: str, label: str = "", created_by: str = "", conn=None) -> int:
    def _create(connection):
        cur = connection.execute(
            "INSERT INTO tenant_tokens (tenant_id, token_hash, label, created_by) VALUES (?, ?, ?, ?)",
            (str(tenant_id), token_hash, label, created_by),
        )
        return int(cur.lastrowid)

    if conn is None:
        with _db().get_db_context() as own_conn:
            return _create(own_conn)
    return _create(conn)


def revoke_tenant_token(token_id: int, conn=None) -> bool:
    def _revoke(connection):
        cur = connection.execute(
            "UPDATE tenant_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE id = ? AND revoked_at IS NULL",
            (int(token_id),),
        )
        return cur.rowcount > 0

    if conn is None:
        with _db().get_db_context() as own_conn:
            return _revoke(own_conn)
    return _revoke(conn)


def get_tenant_webhook_secret(tenant_id: str) -> str:
    """Per-tenant HMAC secret for outbound CRM webhooks.

    Falls back to the tenant's published profile so a secret can live in data
    rather than code. Returns "" when neither is set — the caller must then skip
    signing and log a warning.
    """
    with _db().get_db_context() as conn:
        row = conn.execute(
            "SELECT webhook_secret FROM tenants WHERE id = ?", (str(tenant_id),)
        ).fetchone()
    if row and row["webhook_secret"]:
        return row["webhook_secret"]
    payload = get_current_payload(tenant_id) or {}
    channels = (payload.get("notifications") or {}).get("channels") or []
    for ch in channels:
        if isinstance(ch, dict) and ch.get("type") == "webhook" and ch.get("secret"):
            return str(ch["secret"])
    return ""


def tenant_stats(tenant_id: str) -> Dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "current_version": current_version(tenant_id),
        "versions": list_versions(tenant_id, limit=10),
    }
