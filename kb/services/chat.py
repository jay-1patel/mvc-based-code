"""Admin-to-admin chat: in-memory WebSocket registry + SQLite persistence.

WebSocket protocol (mirrors the React frontend in `frontend/src/components/Chat.jsx`):

Client -> server:
    {"type": "ping"}
    {"type": "load_history", "chat_id": "a|b"}
    {"type": "message", "chat_id": "a|b", "text": "...", "attachment": {...}}
    {"type": "delete_message", "chat_id": "a|b", "message_id": 123, "scope": "me"|"everyone"}

Server -> client:
    {"type": "pong"}
    {"type": "history", "chat_id": "a|b", "messages": [...]}
    {"type": "online_users", "users": [...]}
    {"type": "message", "id": ..., "chat_id": ..., "username": ..., "text": ..., "timestamp": ..., "attachment": ...}
    {"type": "delete_message", "message_id": 123}
    {"type": "system", "text": "..."}
"""
import json
import time
from collections import defaultdict

from routing.config import logger
from ..database import (
    save_chat_message,
    get_chat_history,
    get_chat_last_message,
    delete_chat_message,
    mark_chat_message_deleted,
    list_admins,
)

_connections = defaultdict(list)  # username -> [(websocket, conn_id), ...]


def normalize_chat_id(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def _participants(chat_id: str):
    parts = chat_id.split("|", 1)
    if len(parts) != 2:
        return []
    return parts


async def register(ws, username: str, conn_id: str):
    _connections[username].append((ws, conn_id or "chat"))
    await broadcast_online_users()


async def unregister(ws, username: str):
    _connections[username] = [x for x in _connections.get(username, []) if x[0] is not ws]
    if not _connections[username]:
        _connections.pop(username, None)
    await broadcast_online_users()


async def broadcast_online_users():
    users = list(_connections.keys())
    payload = json.dumps({"type": "online_users", "users": users})
    for user_conns in list(_connections.values()):
        for ws, _ in user_conns:
            try:
                await ws.send_text(payload)
            except Exception:
                # deliberate: one dead websocket must not stop the broadcast to
                # the remaining connections.
                logger.debug("WS_BROADCAST_SEND_SKIPPED | one connection failed", exc_info=True)


async def send_to_user(username: str, payload: dict):
    for ws, _ in list(_connections.get(username, [])):
        try:
            await ws.send_json(payload)
        except Exception:
            # deliberate: one dead websocket must not stop delivery to the
            # user's other connections.
            logger.debug("WS_SEND_FAILED | user=%s", username, exc_info=True)


async def send_to_chat(chat_id: str, payload: dict):
    for user in _participants(chat_id):
        await send_to_user(user, payload)


def _message_out(msg: dict) -> dict:
    """Shape a stored message for the frontend's normalizeMsg()."""
    return {
        "id": msg.get("id"),
        "chat_id": msg.get("chat_id"),
        "username": msg.get("sender") or msg.get("username"),
        "message": msg.get("text") or "",
        "text": msg.get("text") or "",
        "timestamp": msg.get("created_at"),
        "attachment": msg.get("attachment"),
    }


async def handle_load_history(ws, chat_id: str):
    messages = [_message_out(m) for m in get_chat_history(chat_id, limit=200)]
    await ws.send_json({"type": "history", "chat_id": chat_id, "messages": messages})


async def handle_message(username: str, data: dict):
    chat_id = data.get("chat_id", "")
    if len(_participants(chat_id)) != 2:
        await send_to_user(username, {"type": "system", "text": "Invalid chat_id"})
        return

    text = data.get("text", "") or ""
    attachment = data.get("attachment")

    # Reject over-sized attachments (frontend already caps at 10MB)
    if attachment:
        raw = attachment.get("data", "")
        if isinstance(raw, str) and raw.startswith("data:") and len(raw) > 14 * 1024 * 1024:
            await send_to_user(username, {"type": "system", "text": "Attachment too large"})
            return

    msg_id = save_chat_message(chat_id, username, text, attachment)
    stored = get_chat_last_message(chat_id)
    out = _message_out(stored)
    if out.get("id") is None:
        out["id"] = msg_id
    await send_to_chat(chat_id, {"type": "message", **out})


async def handle_delete_message(username: str, data: dict):
    message_id = data.get("message_id")
    scope = data.get("scope", "me")
    if message_id is None:
        return

    if scope == "everyone":
        delete_chat_message(int(message_id))
        await send_to_chat(
            data.get("chat_id", ""),
            {"type": "delete_message", "message_id": message_id},
        )
    else:
        mark_chat_message_deleted(int(message_id), username)


async def handle_client_message(username: str, ws, raw: str):
    try:
        data = json.loads(raw)
    except Exception:
        return

    msg_type = data.get("type")

    if msg_type == "ping":
        await ws.send_json({"type": "pong"})
    elif msg_type == "load_history":
        await handle_load_history(ws, data.get("chat_id", ""))
    elif msg_type == "message":
        await handle_message(username, data)
    elif msg_type == "delete_message":
        await handle_delete_message(username, data)
    elif msg_type == "online_users":
        await ws.send_json({"type": "online_users", "users": list(_connections.keys())})
    else:
        logger.debug(f"chat: unknown message type {msg_type!r} from {username}")


def is_online(username: str) -> bool:
    return bool(_connections.get(username))


def chat_users_with_preview(username: str) -> list:
    """List all other admins with their latest message preview for `username`."""
    result = []
    for admin in list_admins():
        other = admin["username"]
        if other == username:
            continue
        chat_id = normalize_chat_id(username, other)
        last = get_chat_last_message(chat_id)
        result.append({
            "username": other,
            "role": admin["role"],
            "online": is_online(other),
            "last_message": None if not last else {
                "username": last.get("sender"),
                "message": last.get("text") or "",
                "text": last.get("text") or "",
                "created_at": last.get("created_at"),
            },
        })
    return result
