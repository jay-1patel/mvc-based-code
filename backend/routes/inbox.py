import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from database import get_db, get_db_context
from routes.auth import get_current_admin

logger = logging.getLogger("inbox")
router = APIRouter(prefix="/api/inbox")


# ── Models ────────────────────────────────────────────────────────────────

class AssignRequest(BaseModel):
    wa_id: str
    agent_id: str


class HandoverRequest(BaseModel):
    mode: str  # "bot" or "human"


class AgentStatusRequest(BaseModel):
    agent_id: str
    status: str  # "online", "away", "dnd"


# ── Queue ─────────────────────────────────────────────────────────────────

@router.get("/queue")
def get_queue(current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                ch.wa_id,
                MAX(ch.created_at) AS last_message_at,
                (SELECT message FROM chat_history ch2
                 WHERE ch2.wa_id = ch.wa_id ORDER BY ch2.id DESC LIMIT 1) AS last_message,
                (SELECT COUNT(*) FROM chat_history ch3
                 WHERE ch3.wa_id = ch.wa_id) AS unread_count,
                us.state AS bot_state,
                COALESCE(us.human_handover, 0) AS human_handover,
                (SELECT context_json FROM user_states us2
                 WHERE us2.wa_id = ch.wa_id) AS context_json
            FROM chat_history ch
            LEFT JOIN user_states us ON us.wa_id = ch.wa_id
            WHERE us.handover_resolved_at IS NULL
            GROUP BY ch.wa_id
            ORDER BY last_message_at DESC
            """
        ).fetchall()

        conversations = []
        for row in rows:
            wa_id = row["wa_id"]
            handover = bool(row["human_handover"])

            # Look up assignment
            assigned_agent_id = None
            assigned_agent_name = None
            assignment = conn.execute(
                "SELECT agent_id FROM conversation_assignments WHERE wa_id = ?",
                (wa_id,),
            ).fetchone()
            if assignment:
                assigned_agent_id = assignment["agent_id"]
                assigned_agent_name = assignment["agent_id"]

            conversations.append({
                "wa_id": wa_id,
                "name": wa_id,
                "last_message": row["last_message"] or "",
                "last_message_at": row["last_message_at"],
                "unread_count": row["unread_count"] or 0,
                "priority": "normal",
                "assigned_agent_id": assigned_agent_id,
                "assigned_agent_name": assigned_agent_name,
                "handover_mode": "human" if handover else "bot",
            })

        return {"queue": conversations}
    finally:
        conn.close()


# ── Agents ────────────────────────────────────────────────────────────────

@router.get("/agents")
def get_agents(current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                a.username AS agent_id,
                a.username,
                COALESCE(ao.status, 'offline') AS status,
                (SELECT COUNT(DISTINCT us.wa_id)
                 FROM user_states us
                 WHERE us.human_handover = 1
                ) AS active_chats
            FROM admins a
            LEFT JOIN agent_status ao ON ao.agent_id = a.username
            ORDER BY a.username
            """
        ).fetchall()
        agents = [dict(r) for r in rows]
        return {"agents": agents}
    finally:
        conn.close()


@router.put("/agents/status")
def set_agent_status(body: AgentStatusRequest, current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO agent_status (agent_id, status, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at
            """,
            (body.agent_id, body.status, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# ── Assign ────────────────────────────────────────────────────────────────

@router.post("/assign")
def assign_conversation(body: AssignRequest, current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        tid = (current_admin.get("tenant_id") or "").strip() or "default"
        conn.execute(
            """
            INSERT INTO conversation_assignments (tenant_id, wa_id, agent_id, assigned_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
              agent_id=excluded.agent_id, assigned_at=excluded.assigned_at
            """,
            (tid, body.wa_id, body.agent_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return {"status": "ok", "wa_id": body.wa_id, "agent_id": body.agent_id}
    finally:
        conn.close()


# ── Handover toggle ───────────────────────────────────────────────────────

@router.put("/handover/{wa_id}")
def toggle_handover(wa_id: str, body: HandoverRequest, current_admin: dict = Depends(get_current_admin)):
    mode = body.mode
    if mode not in ("bot", "human"):
        raise HTTPException(status_code=400, detail="mode must be 'bot' or 'human'")

    from database import set_human_handover
    set_human_handover(wa_id, active=(mode == "human"))
    return {"status": "ok", "wa_id": wa_id, "mode": mode}


# ── Reset bot state ───────────────────────────────────────────────────────

@router.post("/reset-bot-state/{wa_id}")
def reset_bot_state(wa_id: str, current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE user_states
            SET state = 'MAIN_MENU', context_json = '{}', updated_at = ?
            WHERE wa_id = ?
            """,
            (datetime.utcnow().isoformat(), wa_id),
        )
        conn.commit()
        return {"status": "ok", "wa_id": wa_id}
    finally:
        conn.close()


# ── Resolve handoff (return control to chatbot) ────────────────────────────

RESOLVED_MESSAGE = "Thank you for your patience! Your query has been resolved. The chatbot is now available if you need anything else. 🙏"

@router.post("/resolve/{wa_id}")
def resolve_handoff(wa_id: str, current_admin: dict = Depends(get_current_admin)):
    """Clear human_handover flag so the chatbot resumes handling this conversation.

    Also stamps handover_resolved_at so the conversation is dropped from the live
    human inbox (resolved/closed conversations must not keep showing there).
    """
    from database import set_human_handover
    set_human_handover(wa_id, active=False)

    # Send resolution message to the user via WhatsApp
    try:
        from routing.config import SEND2_USERNAME, SEND2_PASSWORD
        if SEND2_USERNAME and SEND2_PASSWORD:
            from routing.whatsapp import send_whatsapp_message
            send_whatsapp_message(wa_id, RESOLVED_MESSAGE)
    except Exception as e:
        logger.warning(f"Could not send resolve message to {wa_id}: {e}")

    logger.info(f"RESOLVED | {wa_id} | handoff cleared by admin")
    return {"status": "ok", "wa_id": wa_id, "message": RESOLVED_MESSAGE}


# ── Delete conversation ────────────────────────────────────────────────────

@router.delete("/conversation/{wa_id}")
def delete_conversation(wa_id: str, current_admin: dict = Depends(get_current_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_history WHERE wa_id = ?", (wa_id,))
        conn.execute("DELETE FROM user_states WHERE wa_id = ?", (wa_id,))
        conn.execute("DELETE FROM conversation_assignments WHERE wa_id = ?", (wa_id,))
        conn.commit()
        return {"status": "ok", "wa_id": wa_id}
    finally:
        conn.close()
