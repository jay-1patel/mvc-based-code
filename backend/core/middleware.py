"""HTTP middleware and central exception handlers for the application.

Moved out of the entry point: the audit context/mutation middleware, the
request-id middleware (one REQ log line per request), and the central
error handlers. The entry point registers them on the app in the same
order they were defined there; the bodies are unchanged.
"""

import json
import logging
import secrets
import time
import traceback as _traceback

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from routing.config import ADMIN_SECRET_KEY

try:  # backend/ on sys.path (the normal import mode)
    from core.context import request_id_var as _request_id_var
    from database import (
        clear_audit_request_context,
        get_db_context,
        record_admin_audit_event,
        set_audit_request_context,
        trusted_client_ip,
    )
except ImportError:  # imported as part of the `backend` package
    from backend.core.context import request_id_var as _request_id_var
    from backend.database import (
        clear_audit_request_context,
        get_db_context,
        record_admin_audit_event,
        set_audit_request_context,
        trusted_client_ip,
    )

logger = logging.getLogger("router")


async def super_admin_view_only_middleware(request: Request, call_next):
    """Refuse tenant-data writes by a super-admin principal (decided rule).

    The dependency-level guard in routes/auth.py covers every route that
    passes through ``require_tenant_access`` / ``require_permission``. This
    middleware is the same rule for the routers that authenticate ad hoc
    (legacy global surfaces, callbacks, inbox, complaints, orders, catalog):
    for a non-GET request carrying an admin JWT, resolve the caller's role
    and refuse tenant-data writes when the caller is a super admin. Tenant
    API tokens are not JWTs and are unaffected; unauthenticated and
    non-super requests pass straight through to the route's own checks.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return await call_next(request)
    try:
        from routes.auth import is_tenant_data_write
    except ImportError:
        from backend.routes.auth import is_tenant_data_write
    if not is_tenant_data_write(request.method, request.url.path):
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    role = None
    if auth_header.startswith("Bearer "):
        try:
            payload = jwt.decode(
                auth_header.split(" ")[1], ADMIN_SECRET_KEY,
                algorithms=["HS256"], options={"verify_exp": False},
            )
            username = payload.get("sub")
            if username:
                with get_db_context() as conn:
                    row = conn.execute(
                        "SELECT role FROM admins WHERE username = ?", (username,)
                    ).fetchone()
                if row:
                    role = row["role"] if hasattr(row, "keys") else row[0]
        except Exception:
            # A token that does not resolve to an admin is the route's own
            # problem (it will 401/403); this guard only acts on a proven
            # super-admin principal.
            role = None
    if role == "super_admin":
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Super admins are view-only inside tenants: this change must "
                    "come from the tenant's admin (and is approved by you)."
                ),
                "error": "super_admin_view_only",
                "api": f"{request.method} {request.url.path}",
            },
        )
    return await call_next(request)


async def audit_request_context(request: Request, call_next):
    """Provide trusted network metadata to audit writes for this request."""
    peer = request.client.host if request.client else None
    ip = trusted_client_ip(peer, request.headers.get("x-forwarded-for"))
    set_audit_request_context(ip_address=ip, user_agent=request.headers.get("user-agent"))
    try:
        return await call_next(request)
    finally:
        clear_audit_request_context()


async def audit_mutations_middleware(request: Request, call_next):
    if request.method not in ("POST", "PUT", "PATCH", "DELETE") or not request.url.path.startswith("/api/admin"):
        return await call_next(request)

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        body_content = "<multipart_file_upload>"
    else:
        try:
            body_bytes = await request.body()
            async def receive(): return {"type": "http.request", "body": body_bytes}
            request._receive = receive
            body_content = body_bytes.decode('utf-8')
            if "application/json" in content_type:
                body_content = json.loads(body_content)
        except Exception:
            body_content = "<unparseable>"

    response = await call_next(request)

    if 200 <= response.status_code < 300:
        auth_header = request.headers.get("Authorization")
        actor_username = "unknown"
        if auth_header and auth_header.startswith("Bearer "):
            try:
                payload = jwt.decode(auth_header.split(" ")[1], ADMIN_SECRET_KEY, algorithms=["HS256"], options={"verify_exp": False})
                actor_username = payload.get("sub", "unknown")
            except Exception:
                # deliberate: a token that does not decode just means the
                # audit actor stays "unknown".
                logger.debug("AUDIT_ACTOR_DECODE_SKIPPED | actor stays unknown", exc_info=True)
        
        try:
            with get_db_context() as conn:
                record_admin_audit_event(
                    conn,
                    action=f"{request.method} {request.url.path}",
                    actor={"username": actor_username},
                    resource_type="api_endpoint",
                    resource_id=request.url.path,
                    details={"request_body": body_content, "query_params": dict(request.query_params)}
                )
        except Exception as e:
            logger.exception("AUDIT_MUTATION_RECORD_FAILED | %s %s", request.method, request.url.path)

    return response


# ── Phase 1: request-id, one REQ log line per request, central error handlers ──
# Every request gets a short id (req_<8 hex>). It is stored in a contextvar and
# on request.state, returned as the X-Request-Id header, included in every error
# response body, and printed in the app's own REQ log line — so any failure can be
# traced from the browser console line to exactly one backend log line / error row.


def get_request_id() -> str:
    return _request_id_var.get()


def _new_request_id() -> str:
    return "req_" + secrets.token_hex(4)


def _tenant_from_path(path: str):
    parts = [seg for seg in path.split("/") if seg]
    try:
        i = parts.index("tenants")
    except ValueError:
        return None
    return parts[i + 1] if i + 1 < len(parts) else None


def _request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", None) or _request_id_var.get() or _new_request_id()


async def request_id_middleware(request: Request, call_next):
    rid = _new_request_id()
    request.state.request_id = rid
    token = _request_id_var.set(rid)
    started = time.perf_counter()
    api = f"{request.method} {request.url.path}"
    tenant = _tenant_from_path(request.url.path)
    tenant_part = f" | tenant={tenant}" if tenant else ""
    try:
        response = await call_next(request)
    except Exception as exc:
        ms = int((time.perf_counter() - started) * 1000)
        logger.error(f"REQ {rid} | {api} → 500 | {ms}ms{tenant_part} | EXC {type(exc).__name__}")
        raise
    finally:
        _request_id_var.reset(token)
    ms = int((time.perf_counter() - started) * 1000)
    line = f"REQ {rid} | {api} → {response.status_code} | {ms}ms{tenant_part}"
    if response.status_code >= 500:
        logger.error(line)
    else:
        logger.info(line)
    response.headers["X-Request-Id"] = rid
    return response


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Keep the existing status/detail semantics; add request_id + api so the
    caller can quote the exact failing API. Successful responses are untouched."""
    rid = _request_id_for(request)
    api = f"{request.method} {request.url.path}"
    detail = exc.detail
    body = {
        "detail": detail,
        "error": detail if isinstance(detail, str) else f"HTTP {exc.status_code}",
        "request_id": rid,
        "api": api,
    }
    headers = dict(exc.headers or {})
    headers["X-Request-Id"] = rid
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def unhandled_exception_handler(request: Request, exc: Exception):
    """One central handler for unhandled errors: JSON (never plain text), one
    clean ERROR line, and a durable row via the error reporter. The reporter is
    guarded — its failure can never break this response."""
    rid = _request_id_for(request)
    api = f"{request.method} {request.url.path}"
    tenant = _tenant_from_path(request.url.path)
    logger.error(
        f"API_FAIL | req={rid} | api={api} | tenant={tenant or '-'} "
        f"| {type(exc).__name__}: {str(exc)[:300]}"
    )
    logger.debug("Unhandled exception traceback for %s", rid, exc_info=exc)
    try:
        from services.error_reporter import report_error
        report_error(
            "api", api, exc,
            tenant_id=tenant, request_id=rid, http_status=500,
            detail="".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))[-3500:],
        )
    except Exception:
        logger.error(f"ERROR_REPORT_FAILED | req={rid} | api={api}", exc_info=True)
    body = {
        "detail": "Internal Server Error",
        "error": "Internal Server Error",
        "request_id": rid,
        "api": api,
    }
    return JSONResponse(status_code=500, content=body, headers={"X-Request-Id": rid})
