from fastapi import APIRouter, Request, HTTPException
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from kb.services.kb_handler import handle_kb_query
from routing.config import logger

kb_router = APIRouter(prefix="/kb", tags=["kb"])


class KBQueryPayload(BaseModel):
    wa_id: str = Field(..., description="User's WhatsApp ID - required for state machine")
    raw_message: dict = Field(..., description="Raw message data - required for interactive button/list parsing")
    message: Optional[str] = Field(None, description="Text message content (if any)")
    sender_name: str = Field("", description="User's display name")
    force_kb_route: bool = Field(False, description="Force KB routing even for menu keywords")

    class Config:
        json_schema_extra = {
            "example": {
                "wa_id": "919876543210",
                "raw_message": {"type": "text", "text": {"body": "Hello"}},
                "message": "Hello",
                "sender_name": "John Doe",
                "force_kb_route": False,
            }
        }


class MessageRequest(BaseModel):
    message: str
    wa_id: Optional[str] = None
    sender_name: str = ""
    raw_message: Optional[dict] = None


@kb_router.post("/ask")
async def ask(req: KBQueryPayload, http_request: Request):
    user_message = req.message
    if not user_message and req.raw_message:
        if req.raw_message.get("type") == "text" and "text" in req.raw_message:
            user_message = req.raw_message["text"].get("body", "")

    if not user_message and not req.raw_message:
        raise HTTPException(status_code=400, detail="No message provided")

    result = await handle_kb_query(
        wa_id=req.wa_id,
        message=user_message or "",
        sender_name=req.sender_name,
        update_session=True,
        raw_message=req.raw_message,
    )
    return result


@kb_router.post("/kb-answer")
async def kb_answer(req: MessageRequest, http_request: Request):
    user_message = req.message
    if not user_message and req.raw_message:
        if req.raw_message.get("type") == "text" and "text" in req.raw_message:
            user_message = req.raw_message["text"].get("body", "")

    if not user_message and not req.raw_message:
        raise HTTPException(status_code=400, detail="No message provided")

    result = await handle_kb_query(
        wa_id=req.wa_id or "",
        message=user_message or "",
        sender_name=req.sender_name,
        update_session=True,
        raw_message=req.raw_message,
    )
    return result


@kb_router.get("/")
def root():
    return {
        "status": "KB Bot running",
        "version": "2.0.0",
    }
