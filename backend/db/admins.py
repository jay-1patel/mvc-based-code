"""Admin accounts, OTP, files and admin chat messages data access — extracted from backend/database.py."""
import os
import json
import re
import sqlite3
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .core import (
    DB_PATH, BASE_DIR, PROJECT_ROOT, logger, get_db, get_db_context,
    _ensure_columns, _loads_json, _current_tenant, set_request_tenant,
    get_request_tenant
)

from .products import _resolve_tenant
from .audit import record_admin_audit_event


def count_admins() -> int:
    with get_db_context() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM admins").fetchone()
    return row["c"] if row else 0


def create_admin(username: str, password_hash: str, salt: str = "", role: str = "sub_admin",
                 permissions: dict = None, recovery_key: str = None, tenant_id: str = None) -> int:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO admins (username, password_hash, salt, role, permissions, recovery_key, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, password_hash, salt, role,
             json.dumps(permissions or {}), recovery_key, tenant_id),
        )
        return cur.lastrowid


def get_admin_by_username(username: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["permissions"] = _loads_json(d.get("permissions"), {})
    return d


def get_admin_by_id(admin_id: int) -> dict:
    with get_db_context() as conn:
        row = conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["permissions"] = _loads_json(d.get("permissions"), {})
    return d


def list_admins() -> list:
    with get_db_context() as conn:
        rows = conn.execute("SELECT * FROM admins ORDER BY created_at ASC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["permissions"] = _loads_json(d.get("permissions"), {})
        result.append(d)
    return result


def update_admin(username: str, **kwargs) -> bool:
    allowed = {"password_hash", "salt", "role", "permissions", "recovery_key"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    if "permissions" in updates and isinstance(updates["permissions"], dict):
        updates["permissions"] = json.dumps(updates["permissions"])
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [username]
    with get_db_context() as conn:
        conn.execute(f"UPDATE admins SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE username = ?", values)
    return True


def delete_admin(username: str) -> bool:
    with get_db_context() as conn:
        conn.execute("DELETE FROM admins WHERE username = ?", (username,))
    return True


def count_admins_by_role(role: str) -> int:
    with get_db_context() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM admins WHERE role = ?", (role,)).fetchone()
    return row["cnt"] if row else 0


def count_tenant_admins(tenant_id: str, exclude_username: str = None) -> int:
    """Count admin-role accounts in a tenant (optionally ignoring one username)."""
    if not tenant_id:
        return 0
    query = "SELECT COUNT(*) as cnt FROM admins WHERE role = 'admin' AND tenant_id = ?"
    params = [tenant_id]
    if exclude_username:
        query += " AND username != ?"
        params.append(exclude_username)
    with get_db_context() as conn:
        row = conn.execute(query, params).fetchone()
    return row["cnt"] if row else 0


def get_admin_record(username: str, tenant_id: str = None, *, include_unscoped: bool = True) -> dict:
    if tenant_id is None and not include_unscoped:
        return None
    with get_db_context() as conn:
        if tenant_id is None:
            row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM admins WHERE username = ? AND tenant_id = ?",
                (username, tenant_id),
            ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["permissions"] = _loads_json(d.get("permissions"), {})
    return d


def list_admin_records(tenant_id: str = None, *, include_all: bool = True) -> list:
    with get_db_context() as conn:
        if include_all:
            rows = conn.execute("SELECT * FROM admins ORDER BY created_at ASC").fetchall()
        elif not tenant_id:
            return []
        else:
            rows = conn.execute(
                "SELECT * FROM admins WHERE tenant_id = ? ORDER BY created_at ASC",
                (tenant_id,),
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["permissions"] = _loads_json(d.get("permissions"), {})
        result.append(d)
    return result


def update_admin_record(username: str, role: str = None, permissions: dict = None, tenant_id: str = None) -> bool:
    updates = {}
    if role is not None:
        updates["role"] = role
    if permissions is not None:
        updates["permissions"] = json.dumps(permissions)
    if tenant_id is not None:
        updates["tenant_id"] = tenant_id
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [username]
    with get_db_context() as conn:
        conn.execute(f"UPDATE admins SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE username = ?", values)
    return True


def update_admin_email(username: str, email: str = None) -> bool:
    with get_db_context() as conn:
        conn.execute("UPDATE admins SET email = ?, updated_at = CURRENT_TIMESTAMP WHERE username = ?", (email, username))
    return True


def set_admin_otp(username: str, otp_code: str, expires_at: str) -> bool:
    with get_db_context() as conn:
        conn.execute(
            "INSERT INTO admin_otps (username, otp_code, expires_at) VALUES (?, ?, ?)",
            (username, otp_code, expires_at),
        )
    return True


def get_admin_otp(username: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM admin_otps WHERE username = ? ORDER BY id DESC LIMIT 1",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def clear_admin_otp(username: str) -> bool:
    with get_db_context() as conn:
        conn.execute("DELETE FROM admin_otps WHERE username = ?", (username,))
    return True


def save_admin_file(name: str, ext: str, module: str, size: int,
                    doc_id: int = None, file_path: str = None, url: str = None,
                    tenant_id: str = None, actor: dict = None,
                    status: str = "ready", chunks_count: int = 0,
                    error_message: str = None) -> int:
    with get_db_context() as conn:
        effective_tenant = _resolve_tenant(tenant_id) if tenant_id else (get_request_tenant() or tenant_id)
        cur = conn.execute(
            """INSERT INTO admin_files (name, ext, module, size, doc_id, file_path, url, tenant_id, status, chunks_count, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, ext, module, size, doc_id, file_path, url, effective_tenant, status, chunks_count, error_message),
        )
        file_id = cur.lastrowid
        
        # Record audit event for file upload
        try:
            from backend.services.audit_context import actor_from_context
        except ImportError:
            try:
                from services.audit_context import actor_from_context
            except ImportError:
                actor_from_context = lambda explicit=None: explicit
        actor = actor_from_context(actor)
        if actor:
            record_admin_audit_event(
                conn,
                action="file_uploaded",
                actor=actor,
                resource_type="file",
                resource_id=file_id,
                tenant_id=effective_tenant,
                actor_kind=actor.get("type"),
                actor_label=actor.get("label"),
                details={
                    "file_name": os.path.basename(name),
                    "file_ext": ext,
                    "module": module,
                    "file_size": size,
                    "status": status,
                }
            )
        
        return file_id


def update_admin_file_status(
    name: str,
    status: str,
    chunks_count: int = None,
    error_message: str = None,
    tenant_id: str = None,
    bifurcation_stats: dict = None,
) -> bool:
    with get_db_context() as conn:
        effective_tenant = _resolve_tenant(tenant_id) if tenant_id else (get_request_tenant() or tenant_id)
        updates = ["status = ?"]
        params = [status]
        if chunks_count is not None:
            updates.append("chunks_count = ?")
            params.append(chunks_count)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if bifurcation_stats is not None:
            updates.append("bifurcation_stats = ?")
            params.append(json.dumps(bifurcation_stats))

        where = "name = ?"
        params.append(name)
        if effective_tenant:
            where += " AND tenant_id = ?"
            params.append(effective_tenant)

        cur = conn.execute(f"UPDATE admin_files SET {', '.join(updates)} WHERE {where}", params)
        return cur.rowcount > 0


def _parse_admin_file_row(row) -> dict:
    item = dict(row)
    raw_stats = item.get("bifurcation_stats")
    if isinstance(raw_stats, str) and raw_stats.strip():
        try:
            item["bifurcation_stats"] = json.loads(raw_stats)
        except Exception:
            # deliberate: unparseable stats stay as the raw string — the
            # file row itself is still returned.
            logger.debug("ADMIN_FILE_STATS_PARSE_SKIPPED", exc_info=True)
    return item


def list_admin_files(tenant_id: str = None) -> list:
    with get_db_context() as conn:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM admin_files WHERE tenant_id = ? ORDER BY created_at DESC, id DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM admin_files ORDER BY created_at DESC, id DESC"
            ).fetchall()
    return [_parse_admin_file_row(r) for r in rows]


def get_admin_file(name: str, tenant_id: str = None) -> dict:
    with get_db_context() as conn:
        if tenant_id:
            row = conn.execute(
                "SELECT * FROM admin_files WHERE name = ? AND tenant_id = ? ORDER BY id DESC LIMIT 1",
                (name, tenant_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM admin_files WHERE name = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
    return _parse_admin_file_row(row) if row else None


def delete_admin_file(name: str, tenant_id: str = None, actor: dict = None) -> bool:
    with get_db_context() as conn:
        # Get file details before deletion for audit logging
        file_row = None
        if tenant_id:
            file_row = conn.execute(
                "SELECT id, module, size, tenant_id FROM admin_files WHERE name = ? AND tenant_id = ? ORDER BY id DESC LIMIT 1",
                (name, tenant_id),
            ).fetchone()
            conn.execute("DELETE FROM admin_files WHERE name = ? AND tenant_id = ?", (name, tenant_id))
        else:
            file_row = conn.execute(
                "SELECT id, module, size, tenant_id FROM admin_files WHERE name = ? ORDER BY id DESC LIMIT 1",
                (name,),
            ).fetchone()
            conn.execute("DELETE FROM admin_files WHERE name = ?", (name,))
        
        try:
            from backend.services.audit_context import actor_from_context
        except ImportError:
            try:
                from services.audit_context import actor_from_context
            except ImportError:
                actor_from_context = lambda explicit=None: explicit
        actor = actor_from_context(actor)
        # Record audit event for file deletion
        if actor and file_row:
            file_details = dict(file_row)
            record_admin_audit_event(
                conn,
                action="file_deleted",
                actor=actor,
                resource_type="file",
                resource_id=file_details.get("id"),
                tenant_id=tenant_id or file_details.get("tenant_id"),
                actor_kind=actor.get("type"),
                actor_label=actor.get("label"),
                details={
                    "file_name": os.path.basename(name),
                    "module": file_details.get("module", ""),
                    "file_size": file_details.get("size", 0),
                }
            )
        
    return True


def save_admin_chat_message(chat_id: str, username: str, message: str,
                            attachment: dict = None, tenant_id: str = None) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO admin_chat_messages (chat_id, sender, username, text, message, attachment_json, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, username, username, message, message,
             json.dumps(attachment) if attachment else None, now, tenant_id),
        )
        return {"id": cur.lastrowid, "chat_id": chat_id, "username": username,
                "message": message, "text": message, "created_at": now,
                "attachment": attachment, "tenant_id": tenant_id}


def get_admin_chat_history(chat_id: str, username: str = None, limit: int = 200,
                           tenant_id: str = None) -> list:
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    tenant_clause = " AND tenant_id = ?" if tenant_id else ""
    with get_db_context() as conn:
        if username:
            rows = conn.execute(
                f"""SELECT id, chat_id, COALESCE(sender, username) as username,
                          COALESCE(text, message) as message, created_at, attachment_json
                   FROM admin_chat_messages
                   WHERE chat_id = ? AND created_at >= ?{tenant_clause}
                     AND id NOT IN (
                       SELECT message_id FROM deleted_chat_messages WHERE username = ?
                     )
                   ORDER BY id DESC LIMIT ?""",
                ((chat_id, cutoff, tenant_id, username, limit) if tenant_id
                 else (chat_id, cutoff, username, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT id, chat_id, COALESCE(sender, username) as username,
                          COALESCE(text, message) as message, created_at, attachment_json
                   FROM admin_chat_messages
                   WHERE chat_id = ? AND created_at >= ?{tenant_clause}
                   ORDER BY id DESC LIMIT ?""",
                ((chat_id, cutoff, tenant_id, limit) if tenant_id
                 else (chat_id, cutoff, limit)),
            ).fetchall()
        result = []
        for r in reversed(rows):
            item = dict(r)
            if item.get("attachment_json"):
                try:
                    item["attachment"] = json.loads(item["attachment_json"])
                except Exception:
                    item["attachment"] = None
            else:
                item["attachment"] = None
            result.append(item)
        return result


def get_admin_chat_preview(username: str) -> list:
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT acm.chat_id, COALESCE(acm.text, acm.message) as message,
                      COALESCE(acm.sender, acm.username) as username, acm.created_at
               FROM admin_chat_messages acm
               INNER JOIN (
                   SELECT chat_id, MAX(id) as max_id FROM admin_chat_messages
                   WHERE chat_id LIKE ? OR chat_id LIKE ?
                   GROUP BY chat_id
               ) latest ON acm.id = latest.max_id
               ORDER BY acm.created_at DESC""",
            (f"%|{username}", f"{username}|%"),
        ).fetchall()
        return [dict(r) for r in rows]


def _chat_message_to_dict(row) -> dict:
    d = dict(row)
    raw_att = d.pop("attachment_json", None)
    d["attachment"] = _loads_json(raw_att, None) if raw_att else None
    d["username"] = d.get("sender") or d.get("username", "")
    return d


def get_chat_history(chat_id: str, limit: int = 200) -> list:
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT * FROM admin_chat_messages
               WHERE chat_id = ?
               ORDER BY id ASC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
    return [_chat_message_to_dict(r) for r in rows]


def get_chat_last_message(chat_id: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute(
            """SELECT * FROM admin_chat_messages
               WHERE chat_id = ?
               ORDER BY id DESC LIMIT 1""",
            (chat_id,),
        ).fetchone()
    return _chat_message_to_dict(row) if row else None


def save_chat_message(chat_id: str, sender: str, text: str,
                      attachment: dict = None) -> int:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO admin_chat_messages (chat_id, sender, text, attachment_json)
               VALUES (?, ?, ?, ?)""",
            (chat_id, sender, text or "", json.dumps(attachment) if attachment else None),
        )
        return cur.lastrowid


def delete_chat_message(message_id: int) -> bool:
    with get_db_context() as conn:
        conn.execute("DELETE FROM admin_chat_messages WHERE id = ?", (message_id,))
    return True


def mark_chat_message_deleted(message_id: int, username: str) -> bool:
    with get_db_context() as conn:
        conn.execute(
            "UPDATE admin_chat_messages SET deleted_for = ? WHERE id = ?",
            (username, message_id),
        )
    return True


def delete_admin_chat_message(msg_id: int) -> bool:
    with get_db_context() as conn:
        result = conn.execute("DELETE FROM admin_chat_messages WHERE id = ?", (msg_id,))
        return result.rowcount > 0


def soft_delete_message_for_user(msg_id: int, username: str):
    with get_db_context() as conn:
        row = conn.execute("SELECT message FROM admin_chat_messages WHERE id = ?", (msg_id,)).fetchone()
        message = row["message"] if row else None
        conn.execute(
            "INSERT OR IGNORE INTO deleted_chat_messages (message_id, username, message) VALUES (?, ?, ?)",
            (msg_id, username, message),
        )


def soft_delete_message_for_everyone(msg_id: int):
    with get_db_context() as conn:
        row = conn.execute("SELECT message FROM admin_chat_messages WHERE id = ?", (msg_id,)).fetchone()
        message = row["message"] if row else None
        admins = conn.execute("SELECT username FROM admins").fetchall()
        for admin in admins:
            conn.execute(
                "INSERT OR IGNORE INTO deleted_chat_messages (message_id, username, message) VALUES (?, ?, ?)",
                (msg_id, admin["username"], message),
            )


def get_all_admins_except(current_username: str, tenant_id: str = None, *, include_all: bool = True) -> list:
    with get_db_context() as conn:
        if include_all:
            rows = conn.execute(
                "SELECT username FROM admins WHERE username != ? ORDER BY username",
                (current_username,),
            ).fetchall()
        elif not tenant_id:
            return []
        else:
            rows = conn.execute(
                "SELECT username FROM admins WHERE username != ? AND tenant_id = ? ORDER BY username",
                (current_username, tenant_id),
            ).fetchall()
        return [r["username"] for r in rows]
