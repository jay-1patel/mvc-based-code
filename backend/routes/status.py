from fastapi import APIRouter, Query
from routing.config import WHATSAPP_VERIFY_TOKEN, SEND2_USERNAME, SEND2_PASSWORD, SEND2_NUMBER, SEND2_SESSION_MSG_URL
from database import get_db, get_webhook_stats
import requests
import logging

logger = logging.getLogger("chiki_webhook")
router = APIRouter(prefix="/webhook")


@router.get("/logs")
def get_webhook_logs(limit: int = 50, direction: str = None):
    conn = get_db()
    try:
        if direction:
            rows = conn.execute(
                "SELECT * FROM webhook_logs WHERE direction = ? ORDER BY created_at DESC LIMIT ?",
                (direction, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhook_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@router.delete("/logs")
def clear_webhook_logs():
    from database import get_db_context
    with get_db_context() as conn:
        conn.execute("DELETE FROM webhook_logs")
    return {"status": "logs cleared"}


@router.get("/status")
def webhook_status():
    """Public status endpoint — never leaks secrets (see BUG-06 / M6)."""
    return {
        "webhook_url": "/webhook",
        "verify_token_set": bool(WHATSAPP_VERIFY_TOKEN),
        "api_provider": "send2.digital",
        "send2_config": {
            "username_set": bool(SEND2_USERNAME),
            "password_set": bool(SEND2_PASSWORD),
            "number_set": bool(SEND2_NUMBER),
        },
        "routing": "FAQ Assistant + Knowledge Base Assistant (single webhook)",
        "setup": {
            "step_1": "Login to https://cpaas.send2.digital/whatsapp/dev_desk",
            "step_2": "Set Webhook URL to: https://<your-domain>/webhook",
            "step_3": "Fill SEND2_USERNAME, SEND2_PASSWORD, SEND2_NUMBER in .env",
            "step_4": "Start server: python main.py",
            "step_5": "Test via POST /test-message",
        },
    }


@router.get("/health")
def health_check():
    results = {}

    # Check send2.digital API - verify credentials and connectivity only (no real message)
    try:
        resp = requests.post(SEND2_SESSION_MSG_URL, data={
            "user_name": SEND2_USERNAME or "",
            "password": SEND2_PASSWORD or "",
            "contact_no": SEND2_NUMBER or "",
            "message_type": "text",
            "message": "",
        }, timeout=10)
        body = resp.text
        if "Message Sent Successfully" in body:
            send2_status = "sending_ok"
        elif "OPT OUT" in body:
            send2_status = "user_opted_out"
        elif "INVALID PARAMETER" in body.upper():
            send2_status = "reachable"
        elif "invalid" in body.lower() or "error" in body.lower():
            send2_status = "api_error"
        elif "missing" in body.lower() or "required" in body.lower():
            send2_status = "credentials_issue"
        else:
            send2_status = "unknown_response"
        results["send2_api"] = {
            "url": SEND2_SESSION_MSG_URL,
            "http_status": resp.status_code,
            "api_status": send2_status,
            "response": body[:300],
            "ok": resp.status_code == 200 and send2_status in ["sending_ok", "reachable"],
        }
    except Exception as e:
        results["send2_api"] = {"url": SEND2_SESSION_MSG_URL, "api_status": "unreachable", "ok": False, "error": str(e)}

    # Check hkdk.events webhook
    hkdk_url = "https://hkdk.events/63akz9xv0bor2a/webhook"
    try:
        resp = requests.post(hkdk_url, json={"type": "health_check"}, timeout=10)
        body = resp.text
        results["hkdk_webhook"] = {
            "url": hkdk_url,
            "http_status": resp.status_code,
            "response": body[:300],
            "ok": resp.status_code in [200, 201, 202],
        }
    except Exception as e:
        results["hkdk_webhook"] = {"url": hkdk_url, "api_status": "unreachable", "ok": False, "error": str(e)}

    all_ok = all(s.get("ok") for s in results.values())
    return {"all_healthy": all_ok, "services": results}


@router.get("/logs/stats")
def webhook_logs_stats():
    return get_webhook_stats()
