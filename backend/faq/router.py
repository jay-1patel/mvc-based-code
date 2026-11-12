from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from .service import handle_faq_query, build_index, faq_index
from routes.auth import require_super_admin

faq_router = APIRouter(prefix="/faq", tags=["faq"])


class FAQRequest(BaseModel):
    """Request model for FAQ queries"""
    message: str = Field(..., description="Your question for the FAQ bot", min_length=1)
    session_id: Optional[str] = Field(None, description="Optional session identifier")
    tenant_id: Optional[str] = Field(None, description="Optional tenant to answer as; defaults to the sender's or the default tenant")


class FAQResponse(BaseModel):
    """Response model for FAQ answers"""
    answer: str
    confidence: Optional[float] = None
    source: Optional[str] = None
    next_actions: Optional[list] = None


@faq_router.post("/ask", response_model=FAQResponse)
async def ask(request: FAQRequest):
    """Ask a question and get an answer from the FAQ bot"""
    payload = {"message": request.message, "tenant_id": request.tenant_id}
    result = await handle_faq_query(payload)
    return result


@faq_router.post("/admin/rebuild")
async def rebuild(current_admin: dict = Depends(require_super_admin)):
    # build_index is synchronous and returns a dict. Awaiting it raises TypeError.
    return build_index()


@faq_router.post("/faq-answer")
async def faq_answer(request: Request):
    body = await request.json()
    return await handle_faq_query(body)


@faq_router.get("/")
def root():
    return {
        "status":  "FAQ Bot running",
        "vectors": faq_index.index.ntotal if faq_index.is_built else 0,
    }
