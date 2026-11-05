import logging
from typing import Dict, Set

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException

from routing.config import ADMIN_SECRET_KEY
from database import (
    save_admin_chat_message,
    get_admin_chat_history,
    get_all_admins_except,
    get_db_context,
    soft_delete_message_for_user,
    soft_delete_message_for_everyone,
)
from routes.auth import get_current_admin, has_permission, require_permission

logger = logging.getLogger("chiki_webhook")
router = APIRouter()


def make_chat_id(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def _strip_conn_id(raw_username: str) -> str:
    """Strip :conn_id suffix from username (e.g. 'admin1:bell' -> 'admin1')."""
    return raw_username.split(":")[0] if ":" in raw_username else raw_username


class ChatManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, raw_username: str):
        await websocket.accept()
        real = _strip_conn_id(raw_username)
        if real not in self.active_connections:
            self.active_connections[real] = set()
        self.active_connections[real].add(websocket)
        await self.broadcast_online()

    def remove_ws(self, websocket: WebSocket):
        """Remove a specific websocket from all users."""
        for real, conns in list(self.active_connections.items()):
            conns.discard(websocket)
            if not conns:
                del self.active_connections[real]

    async def broadcast(self, message: dict, exclude: WebSocket = None):
        for conns in self.active_connections.values():
            for ws in list(conns):
                if ws is not exclude:
                    try:
                        await ws.send_json(message)
                    except Exception:
                        # deliberate: one dead websocket must not stop the broadcast.
                        logger.debug("WS_BROADCAST_SEND_SKIPPED | broadcast", exc_info=True)

    async def broadcast_online(self):
        online = list(self.active_connections.keys())
        all_ws = set()
        for conns in self.active_connections.values():
            all_ws.update(conns)
        for ws in all_ws:
            try:
                await ws.send_json({"type": "online_users", "users": online})
            except Exception:
                # deliberate: one dead websocket must not stop the broadcast.
                logger.debug("WS_BROADCAST_SEND_SKIPPED | broadcast_online", exc_info=True)

    async def send_message(self, chat_id: str, raw_username: str, text: str, attachment: dict = None):
        real = _strip_conn_id(raw_username)
        saved = save_admin_chat_message(chat_id, real, text, attachment=attachment)
        msg = {
            "type": "message",
            "chat_id": chat_id,
            "username": real,
            "text": text,
            "timestamp": saved["created_at"],
            "id": saved["id"],
            "attachment": attachment,
        }
        all_ws = set()
        for conns in self.active_connections.values():
            all_ws.update(conns)
        for ws in all_ws:
            try:
                await ws.send_json(msg)
            except Exception:
                # deliberate: one dead websocket must not stop the broadcast.
                logger.debug("WS_BROADCAST_SEND_SKIPPED | send_message", exc_info=True)


chat_manager = ChatManager()


def _verify_ws_token(token: str) -> bool:
    if not token:
        return False
    # SECURITY FIX: Removed hardcoded "dev-token" which bypassed all authentication
    # All WebSocket connections must now use valid JWT tokens
    try:
        payload = jwt.decode(token, ADMIN_SECRET_KEY, algorithms=["HS256"])
        # Check token has required claims
        if not payload.get("sub") or not payload.get("exp"):
            return False
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


@router.delete("/api/admin/chat/message/{msg_id}")
def delete_chat_message(msg_id: int, current_admin: dict = Depends(require_permission("chat"))):
    soft_delete_message_for_everyone(msg_id)
    return {"status": "ok"}


@router.post("/api/admin/chat/send")
def send_chat_message_api(
    data: dict,
    current_admin: dict = Depends(require_permission("chat")),
):
    chat_id = data.get("chat_id", "")
    text = data.get("text", "")
    attachment = data.get("attachment")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    saved = save_admin_chat_message(chat_id, current_admin["username"], text, attachment=attachment)
    return {"status": "ok", "message": saved}


@router.get("/api/admin/chat/users")
def list_chat_users(current_admin: dict = Depends(require_permission("chat_history"))):
    username = current_admin["username"]
    admins = get_all_admins_except(username)

    result = []
    for admin in admins:
        cid = make_chat_id(username, admin)
        with get_db_context() as conn:
            row = conn.execute(
                "SELECT username, message, created_at FROM admin_chat_messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
        result.append({
            "username": admin,
            "chat_id": cid,
            "last_message": dict(row) if row else None,
        })

    return {"users": result}


@router.websocket("/api/admin/chat/ws/{username}")
async def chat_websocket(websocket: WebSocket, username: str, token: str = Query(None), conn_id: str = Query("chat")):
    if not _verify_ws_token(token):
        await websocket.close(code=4001)
        return

    raw_username = f"{username}:{conn_id}"
    await chat_manager.connect(websocket, raw_username)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "message" and (data.get("text", "").strip() or data.get("attachment")):
                chat_id = data.get("chat_id", "")
                if not chat_id:
                    continue
                await chat_manager.send_message(
                    chat_id,
                    raw_username,
                    data.get("text", "").strip(),
                    attachment=data.get("attachment"),
                )
            elif data.get("type") == "load_history":
                chat_id = data.get("chat_id", "")
                if chat_id:
                    history = get_admin_chat_history(chat_id, username=username)
                    await websocket.send_json({"type": "history", "chat_id": chat_id, "messages": history})
            elif data.get("type") == "delete_message":
                msg_id = data.get("message_id")
                scope = data.get("scope", "everyone")
                if msg_id is not None:
                    if scope == "me":
                        soft_delete_message_for_user(msg_id, username)
                    else:
                        soft_delete_message_for_everyone(msg_id)
                await chat_manager.broadcast({"type": "delete_message", "message_id": msg_id})
    except WebSocketDisconnect:
        chat_manager.remove_ws(websocket)
        await chat_manager.broadcast_online()
    except Exception as e:
        logger.error(f"Chat WebSocket error: {e}")
        chat_manager.remove_ws(websocket)
        await chat_manager.broadcast_online()
