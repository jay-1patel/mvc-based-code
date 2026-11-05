import os
import sys
import time
import asyncio
import logging
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

# Force PyTorch to use CPU-only mode to prevent CUDA initialization issues
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TORCH_DEVICE"] = "cpu"

_project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "routing"))

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from database import init_db, get_db_context
from services.template_manager import register_all_templates
from routing.config import (
    SEND2_USERNAME, SEND2_PASSWORD, SEND2_NUMBER,
    BUSINESS_NAME, BOT_NAME, logger
)

from backend.core.logging import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    from services.queue import queue, register_queue_handlers
    register_queue_handlers()
    await queue.start()
    
    from routing.config import detect_tunnel_url, PUBLIC_BASE_URL
    detected_url = detect_tunnel_url()
    if detected_url and detected_url != PUBLIC_BASE_URL:
        import routing.config as config
        config.PUBLIC_BASE_URL = detected_url
        logger.info(f"Updated PUBLIC_BASE_URL to: {detected_url}")
    elif not PUBLIC_BASE_URL and detected_url:
        import routing.config as config
        config.PUBLIC_BASE_URL = detected_url
        logger.info(f"Set PUBLIC_BASE_URL to: {detected_url}")
    
    from services.rag import build_chunks_for_kb, rebuild_faiss_index, generate_missing_embeddings
    build_chunks_for_kb()
    generate_missing_embeddings()
    rebuild_faiss_index()

    if SEND2_USERNAME and SEND2_PASSWORD:
        logger.info("Registering all send2 templates...")
        results = register_all_templates()
        success = sum(1 for r in results.values() if r.get("success"))
        logger.info(f"Templates registered: {success}/{len(results)}")
    yield
    await queue.stop()


app = FastAPI(title=f"{BUSINESS_NAME} Assistant", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Rate limiting middleware
_rate_limit_store = {}
RATE_LIMIT_REQUESTS = 30  # per minute
RATE_LIMIT_WINDOW = 60  # seconds

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path in ("/webhook", "/incoming-messages/sync"):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        if client_ip not in _rate_limit_store:
            _rate_limit_store[client_ip] = []
        _rate_limit_store[client_ip] = [t for t in _rate_limit_store[client_ip] if now - t < RATE_LIMIT_WINDOW]
        if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
            return Response(content='{"detail":"Rate limit exceeded"}', status_code=429, media_type="application/json")
        _rate_limit_store[client_ip].append(now)
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()

    body = b""
    async for chunk in request.stream():
        body += chunk

    request._body = body

    response = await call_next(request)
    duration = round(time.time() - start, 4)

    try:
        payload_summary = body[:2000].decode("utf-8", errors="replace")
        # SECURITY FIX: Store payload hash instead of raw payload
        payload_hash = hashlib.sha256(payload_summary.encode('utf-8', errors='replace')).hexdigest()
        payload_size = len(payload_summary)
        with get_db_context() as conn:
            conn.execute(
                "INSERT INTO webhook_logs (direction, endpoint, payload_hash, payload_size, status_code, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "inbound",
                    str(request.url.path),
                    payload_hash,
                    payload_size,
                    response.status_code,
                    f"duration={duration}s method={request.method} client={request.client.host if request.client else '?'}",
                ),
            )
    except Exception as e:
        logger.error(f"Failed to log request: {e}")

    return response


from routes.kb_bot import router as kb_bot_router
from routes.test import router as test_router
from routes.chat import router as chat_router
from routes.incoming import router as incoming_router
from routes.status import router as status_router
from routes.upload import router as upload_router
from routes.webhook import router as webhook_router
from routes.admin import router as admin_router
from routes.frontend import router as frontend_router
from routes.templates import router as templates_router
from routes.api_auth import router as api_auth_router
from routes.api_admin import router as api_admin_router

app.include_router(kb_bot_router, tags=["kb-bot"])
app.include_router(test_router, tags=["test"])
app.include_router(chat_router, tags=["chat"])
app.include_router(incoming_router, tags=["incoming"])
app.include_router(status_router, tags=["status"])
app.include_router(upload_router, tags=["upload"])
app.include_router(webhook_router, tags=["webhook"])
app.include_router(admin_router, tags=["admin"])
app.include_router(frontend_router, tags=["frontend"])
app.include_router(templates_router, tags=["templates"])
app.include_router(api_auth_router, prefix="/api", tags=["api-auth"])
app.include_router(api_admin_router, prefix="/api", tags=["api-admin"])

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_ADMIN_HTML = _STATIC_DIR / "admin" / "index.html"
_ADMIN_BUILD = _STATIC_DIR / "admin_build"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def root():
    return {
        "status": f"{BUSINESS_NAME} Assistant running",
        "version": "2.0.0",
        "admin_panel": "/admin-panel",
    }


@app.get("/admin-panel")
@app.get("/admin-panel/")
def admin_panel():
    if not _ADMIN_HTML.is_file():
        return {"error": "Admin panel not found", "path": str(_ADMIN_HTML)}
    return FileResponse(str(_ADMIN_HTML))


@app.get("/admin")
@app.get("/admin/")
def admin_index():
    index = _ADMIN_BUILD / "index.html"
    if not index.is_file():
        return {"error": "Admin panel build not found. Run `npm run build` in the frontend/ directory."}
    return FileResponse(str(index))


@app.get("/admin/{path:path}")
def admin_spa(path: str):
    base = _ADMIN_BUILD.resolve()
    candidate = (_ADMIN_BUILD / path).resolve()
    if candidate.is_file() and (candidate == base or base in candidate.parents):
        return FileResponse(str(candidate))
    index = _ADMIN_BUILD / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return {"error": "Admin panel not found"}


@app.post("/send-message")
async def send_message(request: Request):
    from services.whatsapp import send_whatsapp_message
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid or empty JSON body"}
    to = body.get("to", "") or body.get("number", "")
    text = body.get("text", "") or body.get("message", "")
    message_type = body.get("message_type", "text")

    if not to or not text:
        return {"status": "error", "detail": "to and text required"}

    if not SEND2_USERNAME or not SEND2_PASSWORD:
        return {"status": "error", "detail": "SEND2 credentials not set in .env"}

    success = await asyncio.to_thread(send_whatsapp_message, to, text, message_type)
    return {"status": "sent" if success else "failed", "to": to, "text": text}


@app.post("/send-menu")
async def send_menu(request: Request):
    from services.whatsapp import send_menu as send_menu_msg
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid or empty JSON body"}
    to = body.get("to", "") or body.get("number", "")

    if not to:
        return {"status": "error", "detail": "to required"}

    if not SEND2_USERNAME or not SEND2_PASSWORD:
        return {"status": "error", "detail": "SEND2 credentials not set in .env"}

    success = await asyncio.to_thread(send_menu_msg, to)
    return {"status": "sent" if success else "failed", "to": to}


@app.post("/send-media")
async def send_media(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid or empty JSON body"}

    to = body.get("to", "")
    media_type = body.get("type", "")
    url = body.get("url", "")

    if not to or not url:
        return {"status": "error", "detail": "to and url required"}

    valid_types = ("image", "video", "audio", "document", "sticker")
    if media_type not in valid_types:
        return {"status": "error", "detail": f"type must be one of {valid_types}"}

    if not SEND2_USERNAME or not SEND2_PASSWORD:
        return {"status": "error", "detail": "SEND2 credentials not set in .env"}

    from services.whatsapp import send_image, send_video, send_audio, send_document, send_sticker
    funcs = {
        "image": send_image,
        "video": send_video,
        "audio": send_audio,
        "document": send_document,
        "sticker": send_sticker,
    }
    success = await asyncio.to_thread(funcs[media_type], to, url)
    return {"status": "sent" if success else "failed", "to": to, "type": media_type}


@app.post("/send-product")
async def send_product(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid or empty JSON body"}

    to = body.get("to", "")
    product_id = body.get("product_id")
    slug = body.get("slug")
    preview = body.get("preview", False)

    if not product_id and not slug:
        return {"status": "error", "detail": "product_id or slug required"}

    from database import get_product, get_product_by_slug
    from services.whatsapp import send_product_with_image, _build_product_message

    if product_id:
        product = get_product(product_id)
    else:
        product = get_product_by_slug(slug)

    if not product:
        return {"status": "error", "detail": "Product not found"}

    if preview:
        msg = _build_product_message(product)
        media_url = product.get("media_url")
        return {
            "status": "preview",
            "product": product.get("name"),
            "media_url": media_url,
            "message": msg,
        }

    if not to:
        return {"status": "error", "detail": "to required when preview=false"}
    if not SEND2_USERNAME or not SEND2_PASSWORD:
        return {"status": "error", "detail": "SEND2 credentials not set in .env"}

    success = await asyncio.to_thread(send_product_with_image, to, product)
    return {"status": "sent" if success else "failed", "to": to, "product": product.get("name")}


@app.post("/send-interactive")
async def send_interactive(request: Request):
    from services.whatsapp import send_interactive_list, send_interactive_buttons
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid or empty JSON body"}

    to = body.get("to", "") or body.get("number", "")
    msg_type = body.get("type", "list")
    header = body.get("header")
    body_text = body.get("body", "") or body.get("body_text", "")
    footer = body.get("footer")
    sections = body.get("sections", [])
    buttons = body.get("buttons", [])

    if not to or not body_text:
        return {"status": "error", "detail": "to and body required"}

    if not SEND2_USERNAME or not SEND2_PASSWORD:
        return {"status": "error", "detail": "SEND2 credentials not set in .env"}

    if msg_type == "list" and sections:
        success = await asyncio.to_thread(
            send_interactive_list,
            to=to,
            body_text=body_text,
            button_text=body.get("button", "Options"),
            sections=sections,
            header_text=header,
            footer_text=footer,
        )
    elif msg_type == "buttons" and buttons:
        success = await asyncio.to_thread(
            send_interactive_buttons,
            to=to,
            body_text=body_text,
            buttons=buttons,
            header_text=header,
            footer_text=footer,
        )
    else:
        return {"status": "error", "detail": "type=list requires sections; type=buttons requires buttons"}

    return {"status": "sent" if success else "failed", "to": to, "type": msg_type}



def _silence_proactor_connection_reset():
    """Suppress benign asyncio 'WinError 10054 ConnectionResetError' spam on Windows.

    Occurs when a client (e.g. browser closing the admin panel) aborts a connection
    while a response is in flight — the Proactor transport logs it as an error even
    though the request already completed successfully.
    """
    try:
        import socket as _socket
        from asyncio import proactor_events

        original = proactor_events._ProactorBasePipeTransport._call_connection_lost

        def patched(self, exc):
            try:
                if not self._called_connection_lost:
                    try:
                        self._protocol.connection_lost(exc)
                    finally:
                        if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
                            try:
                                self._sock.shutdown(_socket.SHUT_RDWR)
                            except OSError:
                                # deliberate (vendored asyncio transport patch): the socket is
                                # already going away — shutdown errors are expected.
                                logger.debug("PROACTOR_SOCKET_SHUTDOWN_SKIPPED", exc_info=True)
                        self._sock.close()
                        self._sock = None
                        server = self._server
                        if server is not None:
                            server._detach()
                            self._server = None
                        self._called_connection_lost = True
            except Exception:
                # deliberate (vendored asyncio transport patch): teardown continues
                # regardless — this runs inside connection_lost.
                logger.debug("PROACTOR_CONNECTION_LOST_STEP_SKIPPED", exc_info=True)

        proactor_events._ProactorBasePipeTransport._call_connection_lost = patched
        logger.debug("Patched asyncio Proactor transport to ignore connection reset errors")
    except Exception as e:
        logger.debug(f"Could not patch asyncio Proactor transport: {e}")


@app.get("/rate-limiter-stats")
async def get_rate_limiter_stats():
    """Get rate limiter statistics to monitor API usage and performance."""
    from services.rate_limiter import groq_rate_limiter
    from services.brain import _response_cache

    stats = groq_rate_limiter.get_stats()
    stats["cache_size"] = len(_response_cache)
    stats["cache_hit_rate"] = f"{(groq_rate_limiter.rate_limited_count / max(groq_rate_limiter.total_requests, 1) * 100):.1f}%"

    return {
        "status": "success",
        "stats": stats,
        "recommendations": []
    }



