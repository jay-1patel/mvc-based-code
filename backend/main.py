import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "routing"))

import logging
import asyncio
import secrets
import time
import traceback as _traceback
from contextvars import ContextVar
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import json
import jwt
from routing.config import ADMIN_SECRET_KEY
from database import get_db_context, record_admin_audit_event
from fastapi.middleware.cors import CORSMiddleware
from middleware import SecurityHeadersMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from database import (
    init_db,
    set_audit_request_context,
    clear_audit_request_context,
    trusted_client_ip,
)
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

from routing.config import ROUTER_PORT
from routing import classify_query
from routing.message import forward_to_bot
from routes.chat import router as chat_router
from routes.faqs import router as faqs_router
from routes.status import router as status_router
from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.auth import RATE_LIMIT_ENABLED, limiter, rate_limit_exceeded_handler
try:
    from slowapi.errors import RateLimitExceeded
except ImportError:
    RateLimitExceeded = None
from routes.chat_admin import router as chat_admin_router
from routing import webhook_router
from faq.router import faq_router
from kb.router import kb_router
from routes.catalog import router as catalog_router
from routes.distributors import router as distributors_router
from routes.campaigns import router as campaigns_router
from routes.unified import router as unified_router
from routes.inbox import router as inbox_router
from routes.dynamic_config import router as dynamic_config_router
from routes.orders import router as orders_router
from routes.complaints import router as complaints_router
from routes.tenants import router as tenants_router
from routes.crawl import router as crawl_router
from routes.offerings import router as offerings_router
from routes.conversations import router as conversations_router
from routes.tenant_files import router as tenant_files_router
from routes.tenant_chat import router as tenant_chat_router
from routes.tenant_operations import router as tenant_operations_router
from routes.leads import router as leads_router
from routes.api_onboarding import router as api_onboarding_router
from routes.onboarding_kits import router as onboarding_kits_router
from routes.audit import router as audit_router
from routes.audit_logs import router as audit_logs_router
from routes.tenant_approvals import router as tenant_approvals_router
from routes.integrations import router as integrations_router
from routes.callbacks import router as callbacks_router
from routes.google_connect import router as google_connect_router
from routes.test_simulator import router as test_simulator_router
from routes.api_errors import router as api_errors_router
from services.handoff_timeout import handoff_timeout_monitor
from core.jobs import lifespan
from core.middleware import (
    audit_mutations_middleware,
    audit_request_context,
    get_request_id,
    http_exception_handler,
    request_id_middleware,
    super_admin_view_only_middleware,
    unhandled_exception_handler,
)
from routes.root import build_admin_router
from routes.root import router as root_router

from core.logging import setup_logging

setup_logging()
logger = logging.getLogger("router")

# httpx logs one INFO line per outgoing request (e.g. the send2.digital inbound
# poll that runs every few seconds), which floods the console. Keep our own logs
# but silence the HTTP transport chatter.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"

app = FastAPI(
    title="Chiki Unified Service",
    version="2.0.0", 
    lifespan=lifespan,
    # SECURITY FIX: Enforce HTTPS for all connections
    https_redirect=FORCE_HTTPS,
)

try:
    from services.audit_context import install_writer_context
except ImportError:
    from backend.services.audit_context import install_writer_context
import database as _audit_database
install_writer_context(_audit_database)


app.middleware("http")(super_admin_view_only_middleware)
app.middleware("http")(audit_request_context)
app.middleware("http")(audit_mutations_middleware)
app.middleware("http")(request_id_middleware)
app.exception_handler(StarletteHTTPException)(http_exception_handler)
app.exception_handler(Exception)(unhandled_exception_handler)
# slowapi requires the limiter on app.state plus an app-level exception handler
if RATE_LIMIT_ENABLED:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    # SECURITY FIX: Restricted CORS to specific trusted origins only
    # Wildcard (*) allows any website to make cross-origin requests
    allow_origins=os.getenv("CORS_ORIGINS", "https://your-production-domain.com,https://your-admin-domain.com,http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Accept-Language", "X-Requested-With"],
    allow_credentials=True,
)

# SECURITY FIX: Add security headers to prevent common web vulnerabilities
app.add_middleware(
    SecurityHeadersMiddleware,
    content_security_policy="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:;",
    force_https=FORCE_HTTPS,
    frame_options="DENY",
    content_type_nosniff=True,
    strict_transport_security="max-age=31536000; includeSubDomains; preload",
    referrer_policy="strict-origin-when-cross-origin",
    x_xss_protection="1; mode=block",
    x_content_type_options="nosniff",
    x_frame_options="DENY",
)

# SECURITY FIX: Enforce HTTPS redirect (in addition to FastAPI's https_redirect option)
# This provides an additional layer of protection
if FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

app.include_router(chat_router, tags=["chat"])
app.include_router(faqs_router, tags=["faqs"])
app.include_router(status_router, tags=["status"])
app.include_router(admin_router, tags=["admin"])
app.include_router(auth_router, tags=["auth"])
app.include_router(chat_admin_router, tags=["chat-admin"])
app.include_router(webhook_router, prefix="/webhook", tags=["webhook"])
app.include_router(faq_router, tags=["faq"])
app.include_router(kb_router, tags=["kb"])
app.include_router(catalog_router, tags=["catalog"])
app.include_router(distributors_router, tags=["distributors"])
app.include_router(campaigns_router, tags=["campaigns"])
app.include_router(unified_router, tags=["unified"])
app.include_router(inbox_router, tags=["inbox"])
app.include_router(dynamic_config_router, tags=["dynamic-config"])
app.include_router(orders_router, tags=["orders"])
app.include_router(complaints_router, tags=["complaints"])
app.include_router(tenants_router, tags=["tenants"])
app.include_router(crawl_router, tags=["crawl"])
app.include_router(offerings_router, tags=["offerings"])
app.include_router(conversations_router, tags=["conversations"])
app.include_router(tenant_files_router, tags=["tenant-files"])
app.include_router(tenant_chat_router, tags=["tenant-chat"])
app.include_router(tenant_operations_router, tags=["tenant-operations"])
app.include_router(leads_router, tags=["leads"])
app.include_router(api_onboarding_router, tags=["api-onboarding"])
app.include_router(onboarding_kits_router, tags=["onboarding-kits"])
app.include_router(audit_router, tags=["audit-history"])
app.include_router(audit_logs_router, tags=["audit-logs"])
app.include_router(tenant_approvals_router, tags=["tenant-approvals"])
app.include_router(integrations_router, tags=["integrations"])
app.include_router(callbacks_router, tags=["callbacks"])
app.include_router(google_connect_router, tags=["google-connect"])
app.include_router(test_simulator_router, tags=["test-simulator"])
app.include_router(api_errors_router, tags=["api-errors"])

IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")
if os.path.isdir(IMAGES_DIR):
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# D5: serve the RESOLVED uploads root (routing.config.UPLOAD_DIR honours the
# UPLOAD_DIR env var) so files stored externally stay reachable at the same
# /uploaded_files/... URLs.
from routing.config import UPLOAD_DIR as _RESOLVED_UPLOAD_DIR
UPLOADED_DIR = _RESOLVED_UPLOAD_DIR
os.makedirs(UPLOADED_DIR, exist_ok=True)
app.mount("/uploaded_files", StaticFiles(directory=UPLOADED_DIR), name="uploaded-files")

# Company logos live on disk and are served under /api so the Vite dev proxy
# (which only forwards /api) reaches them; no third-party host or key needed.
LOGOS_DIR = os.path.join(UPLOADED_DIR, "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)
app.mount("/api/logos", StaticFiles(directory=LOGOS_DIR), name="logos")

# Chat attachments (admin chat + live inbox) are stored on disk and served the
# same way: local hosting, no third-party host or API key.
CHAT_ATTACHMENTS_DIR = os.path.join(UPLOADED_DIR, "chat_attachments")
os.makedirs(CHAT_ATTACHMENTS_DIR, exist_ok=True)
app.mount("/api/uploads/chat", StaticFiles(directory=CHAT_ATTACHMENTS_DIR), name="chat-attachments")

# Product images & PDF brochures extracted from catalogs
PRODUCT_IMAGES_DIR = os.path.join(UPLOADED_DIR, "product_images")
os.makedirs(PRODUCT_IMAGES_DIR, exist_ok=True)
app.mount("/api/uploads/product_images", StaticFiles(directory=PRODUCT_IMAGES_DIR), name="product-images")

BROCHURES_DIR = os.path.join(UPLOADED_DIR, "brochures")
os.makedirs(BROCHURES_DIR, exist_ok=True)
app.mount("/api/uploads/brochures", StaticFiles(directory=BROCHURES_DIR), name="brochures")

_backend_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_backend_dir)
_frontend_dist = os.path.join(_project_root, "frontend", "dist")
if os.path.isdir(_frontend_dist) and os.path.isfile(os.path.join(_frontend_dist, "index.html")):
    ADMIN_BUILD_DIR = _frontend_dist
else:
    ADMIN_BUILD_DIR = os.path.join(_backend_dir, "admin_build")




app.include_router(root_router)


if os.path.isdir(ADMIN_BUILD_DIR):
    admin_assets_dir = os.path.join(ADMIN_BUILD_DIR, "assets")
    if os.path.isdir(admin_assets_dir):
        app.mount("/assets", StaticFiles(directory=admin_assets_dir), name="admin-assets")
    app.include_router(build_admin_router(ADMIN_BUILD_DIR))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=ROUTER_PORT, reload=False)


