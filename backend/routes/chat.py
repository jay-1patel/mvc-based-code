import logging
import os
import json
import mimetypes

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from database import get_db, get_db_context, save_chat
from routes.auth import get_current_admin
from routing.config import SEND2_USERNAME, SEND2_PASSWORD, SEND2_NUMBER, SEND_MEDIA, IMGHIPPO_API_KEY

logger = logging.getLogger("chat")
router = APIRouter(prefix="/api/chat")

CATBOX_API_URL = "https://catbox.moe/user/api.php"
IMGHIPPO_API_URL = "https://api.imghippo.com/v1/upload"


class SendMessageRequest(BaseModel):
    wa_id: str
    message: str
    sender_type: str = "agent"
    sender: str = "admin"


# ── Chat history for a WhatsApp user ──────────────────────────────────────

@router.get("/history/{wa_id}")
def get_chat_history_for_user(wa_id: str, current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, wa_id, sender_name, message, response, route, created_at
            FROM chat_history
            WHERE wa_id = ?
            ORDER BY id ASC
            LIMIT 200
            """,
            (wa_id,),
        ).fetchall()

        messages = []
        for row in rows:
            if row["message"]:
                messages.append({
                    "id": row["id"],
                    "role": "user",
                    "sender": row["sender_name"] or row["wa_id"],
                    "text": row["message"],
                    "created_at": row["created_at"],
                })
            if row["response"]:
                messages.append({
                    "id": row["id"],
                    "role": "bot",
                    "sender": "bot",
                    "text": row["response"],
                    "created_at": row["created_at"],
                })

        return {"messages": messages}
    finally:
        conn.close()


# ── Upload helpers ─────────────────────────────────────────────────────────

def _upload_to_catbox(content: bytes, filename: str) -> str | None:
    """Upload file to catbox.moe, returns public URL."""
    try:
        resp = httpx.post(
            CATBOX_API_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, content, "application/octet-stream")},
            timeout=30,
        )
        if resp.is_success and not resp.text.startswith("error"):
            return resp.text.strip()
    except Exception as e:
        logger.error(f"Catbox upload failed: {e}")
    return None


# The implementation lives in the services layer (services/image_upload.py)
# so services no longer import from this route module. Re-exported here so
# existing `from routes.chat import _upload_to_imghippo` callers keep working.
try:
    from services.image_upload import _upload_to_imghippo
except ImportError:
    from backend.services.image_upload import _upload_to_imghippo


def _guess_media_type(filename: str, content_type: str = "") -> str:
    """Determine WhatsApp media_type from filename/content-type."""
    ct = content_type.lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return "image"
    if ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        return "video"
    return "document"


# ── Send via send2.digital ────────────────────────────────────────────────

def _send_text_via_send2(wa_id: str, text: str) -> dict:
    """Send text message via send2.digital."""
    url = "https://api.send2.digital/devdesk/session-msg-send"
    payload = {
        "user_name": SEND2_USERNAME,
        "password": SEND2_PASSWORD,
        "contact_no": wa_id,
        "message_type": "text",
        "message": text,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"send2 text error: {e}")
        return {"error": str(e)}


def _send_media_via_send2(wa_id: str, media_type: str, media_url: str, caption: str = None) -> dict:
    """Send media message via send2.digital."""
    url = "https://api.send2.digital/devdesk/session-msg-send"
    payload = {
        "user_name": SEND2_USERNAME,
        "password": SEND2_PASSWORD,
        "contact_no": wa_id,
        "message_type": media_type,
        "message": media_url,
    }
    if caption:
        payload["caption"] = caption
    try:
        resp = httpx.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"send2 media error: {e}")
        return {"error": str(e)}


# ── Send message from agent to user ───────────────────────────────────────

@router.post("/send")
async def send_message(
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    content_type = request.headers.get("content-type", "")

    wa_id = None
    message = ""
    sender = current_admin.get("username", "agent")
    uploaded_file = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        wa_id = form.get("wa_id")
        message = form.get("caption", "") or ""
        uploaded_file = form.get("file")
    else:
        body = await request.json()
        wa_id = body.get("wa_id")
        message = body.get("message", "")
        sender = body.get("sender", sender)

    if not wa_id:
        raise HTTPException(status_code=400, detail="wa_id is required")

    result = {}

    if uploaded_file and hasattr(uploaded_file, "read"):
        # Read file content
        file_content = await uploaded_file.read()
        filename = uploaded_file.filename or "file"
        file_ct = getattr(uploaded_file, "content_type", "") or ""

        # Host publicly so Meta/send2 can fetch the file.
        # Images → img402/BasePic (free); PDFs → Cloudflare PUBLIC_BASE_URL.
        media_type = _guess_media_type(filename, file_ct)
        subdir = "chat_attachments" if media_type != "image" else "product_images"
        try:
            from services.media_host import upload_public_media
        except ImportError:
            from backend.services.media_host import upload_public_media
        media_url = upload_public_media(
            file_content, filename, file_ct, subdir=subdir
        )
        if not media_url:
            raise HTTPException(
                status_code=500,
                detail=(
                    "File upload failed. For PDFs set a live PUBLIC_BASE_URL "
                    "(Cloudflare tunnel). For images, img402/BasePic must be reachable."
                ),
            )

        # Send media via send2.digital
        result = _send_media_via_send2(wa_id, media_type, media_url, caption=message or None)

        # Save to chat_history
        save_chat(wa_id, sender, message or f"[{media_type}] {filename}", response=None, route="agent")
    else:
        # Text-only message
        result = _send_text_via_send2(wa_id, message)
        save_chat(wa_id, sender, message, response=None, route="agent")

    # A human agent has just responded: keep the handoff alive by resetting the
    # auto-revert timer. If this conversation is not in human_handover mode the
    # row is simply left untouched (no-op update).
    _touch_handover_activity(wa_id)

    return {"status": "ok", "wa_id": wa_id, "result": result}


def _touch_handover_activity(wa_id: str) -> None:
    """Refresh user_states.updated_at so the 1h auto-revert timeout is measured
    from the last human-agent reply rather than from the handoff start."""
    try:
        from database import get_db_context
        with get_db_context() as conn:
            conn.execute(
                """
                UPDATE user_states
                SET updated_at = CURRENT_TIMESTAMP
                WHERE wa_id = ? AND human_handover = 1
                """,
                (wa_id,),
            )
    except Exception as e:
        logger.warning(f"Failed to touch handover activity for {wa_id}: {e}")
