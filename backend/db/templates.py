"""Message templates and session buttons data access — extracted from backend/database.py."""
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


def save_template(name: str, data: dict) -> int:
    with get_db_context() as conn:
        existing = conn.execute(
            "SELECT id FROM whatsapp_templates WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE whatsapp_templates SET
                    category = ?, subcategory = ?, header_type = ?,
                    header_text = ?, body_template = ?, footer = ?,
                    buttons_json = ?, params_json = ?, language = ?,
                    template_for = ?, status = ?, send2_response = ?,
                    updated_at = CURRENT_TIMESTAMP
                   WHERE name = ?""",
                (
                    data.get("category", "UTILITY"),
                    data.get("subcategory", "custom"),
                    data.get("header_type", "noheader"),
                    data.get("header_text", ""),
                    data.get("body_template", ""),
                    data.get("footer", ""),
                    json.dumps(data.get("buttons", [])),
                    json.dumps(data.get("params", [])),
                    data.get("language", "en"),
                    data.get("template_for", "sales"),
                    data.get("status", "registered"),
                    data.get("send2_response", ""),
                    name,
                ),
            )
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO whatsapp_templates
                    (name, category, subcategory, header_type, header_text,
                     body_template, footer, buttons_json, params_json,
                     language, template_for, status, send2_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    data.get("category", "UTILITY"),
                    data.get("subcategory", "custom"),
                    data.get("header_type", "noheader"),
                    data.get("header_text", ""),
                    data.get("body_template", ""),
                    data.get("footer", ""),
                    json.dumps(data.get("buttons", [])),
                    json.dumps(data.get("params", [])),
                    data.get("language", "en"),
                    data.get("template_for", "sales"),
                    data.get("status", "registered"),
                    data.get("send2_response", ""),
                ),
            )
            return cur.lastrowid


def get_template(name: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_templates WHERE name = ?", (name,)
        ).fetchone()
    if row:
        result = dict(row)
        try:
            result["buttons_json"] = json.loads(result.get("buttons_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            result["buttons_json"] = []
        try:
            result["params_json"] = json.loads(result.get("params_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            result["params_json"] = []
        return result
    return None


def list_templates(status: str = None, category: str = None, limit: int = 50) -> list:
    query = "SELECT * FROM whatsapp_templates WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["buttons_json"] = json.loads(d.get("buttons_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["buttons_json"] = []
        results.append(d)
    return results


def delete_template(name: str) -> bool:
    with get_db_context() as conn:
        conn.execute("DELETE FROM whatsapp_templates WHERE name = ?", (name,))
    return True


def save_session_button(button_id: str, wa_id: str = None, session_id: str = None,
                        button_type: str = "quick", label: str = "",
                        payload: dict = None, state: str = "MAIN_MENU",
                        lang: str = "en", expires_at: str = None) -> int:
    payload_json = json.dumps(payload or {})
    with get_db_context() as conn:
        existing = conn.execute(
            "SELECT id FROM session_buttons WHERE button_id = ?", (button_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE session_buttons SET
                    wa_id = ?, session_id = ?, button_type = ?, label = ?,
                    payload_json = ?, state = ?, lang = ?,
                    expires_at = ?, is_active = 1, triggered_at = NULL
                   WHERE button_id = ?""",
                (wa_id, session_id, button_type, label, payload_json,
                 state, lang, expires_at, button_id),
            )
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO session_buttons
                    (button_id, wa_id, session_id, button_type, label,
                     payload_json, state, lang, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (button_id, wa_id, session_id, button_type, label,
                 payload_json, state, lang, expires_at),
            )
            return cur.lastrowid


def get_session_button(button_id: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM session_buttons WHERE button_id = ?", (button_id,)
        ).fetchone()
    if row:
        result = dict(row)
        try:
            result["payload_json"] = json.loads(result.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            result["payload_json"] = {}
        return result
    return None


def list_session_buttons(wa_id: str = None, state: str = None,
                         is_active: bool = True, limit: int = 50) -> list:
    query = "SELECT * FROM session_buttons WHERE 1=1"
    params = []
    if wa_id:
        query += " AND wa_id = ?"
        params.append(wa_id)
    if state:
        query += " AND state = ?"
        params.append(state)
    if is_active:
        query += " AND is_active = 1"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["payload_json"] = json.loads(d.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["payload_json"] = {}
        results.append(d)
    return results


def deactivate_session_button(button_id: str) -> bool:
    with get_db_context() as conn:
        conn.execute(
            "UPDATE session_buttons SET is_active = 0 WHERE button_id = ?",
            (button_id,),
        )
    return True


def trigger_session_button(button_id: str, wa_id: str) -> bool:
    with get_db_context() as conn:
        conn.execute(
            "UPDATE session_buttons SET triggered_at = CURRENT_TIMESTAMP WHERE button_id = ?",
            (button_id,),
        )
    return True


def log_button_click(button_id: str, wa_id: str, session_id: str = None,
                     button_type: str = None, label: str = None,
                     payload: dict = None, source: str = "whatsapp",
                     metadata: dict = None) -> int:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO button_clicks
                (button_id, wa_id, session_id, button_type, label,
                 payload_json, source, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (button_id, wa_id, session_id, button_type, label,
             json.dumps(payload or {}), source, json.dumps(metadata or {})),
        )
        return cur.lastrowid


def get_button_clicks(button_id: str = None, wa_id: str = None,
                      limit: int = 50) -> list:
    query = "SELECT * FROM button_clicks WHERE 1=1"
    params = []
    if button_id:
        query += " AND button_id = ?"
        params.append(button_id)
    if wa_id:
        query += " AND wa_id = ?"
        params.append(wa_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["payload_json"] = json.loads(d.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["payload_json"] = {}
        try:
            d["metadata_json"] = json.loads(d.get("metadata_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["metadata_json"] = {}
        results.append(d)
    return results


def get_button_click_stats(button_id: str = None, wa_id: str = None) -> dict:
    query = "SELECT COUNT(*) as total, button_id, label FROM button_clicks WHERE 1=1"
    params = []
    if button_id:
        query += " AND button_id = ?"
        params.append(button_id)
    if wa_id:
        query += " AND wa_id = ?"
        params.append(wa_id)
    query += " GROUP BY button_id, label ORDER BY total DESC"
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
    return {"total_clicks": sum(r["total"] for r in rows), "by_button": [dict(r) for r in rows]}
