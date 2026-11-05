"""Root-level routes that used to be defined in the entry point.

The status, legacy chat-history, classify and forward handlers, plus a
builder for the admin single-page-app router (registered by the entry
point only when a built admin UI exists on disk). Bodies are unchanged
from the entry point.
"""

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from routing import classify_query
from routing.message import forward_to_bot

router = APIRouter()


@router.get("/")
def root():
    return {
        "status": "Unified Bot Service running",
    }


@router.get("/chat-history")
def legacy_chat_history():
    """Legacy compatibility route used by old admin panel."""
    from database import get_db
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM chat_history ORDER BY created_at DESC LIMIT 50").fetchall()
        return {"history": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.post("/classify")
async def classify(request: Request):
    body = await request.json()
    text = body.get("message", "")
    return {"message": text, "type": classify_query(text)}


@router.post("/forward")
async def forward(request: Request):
    body = await request.json()
    text = body.get("message", "")
    qtype = body.get("type") or classify_query(text)
    answer = await forward_to_bot(qtype, text)
    return {"type": qtype, "answer": answer}


def build_admin_router(admin_build_dir: str) -> APIRouter:
    """Router serving the built admin UI from ``admin_build_dir``."""
    admin_router = APIRouter()

    @admin_router.get("/admin")
    @admin_router.get("/admin/{full_path:path}")
    def serve_admin(full_path: str = ""):
        file_path = os.path.join(admin_build_dir, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(admin_build_dir, "index.html"))

    return admin_router
