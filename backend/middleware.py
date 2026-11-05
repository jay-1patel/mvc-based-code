from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        content_security_policy: str = "default-src 'self'",
        force_https: bool = False,
        frame_options: str = "DENY",
        content_type_nosniff: bool = True,
        strict_transport_security: str = "max-age=31536000; includeSubDomains; preload",
        referrer_policy: str = "strict-origin-when-cross-origin",
        x_xss_protection: str = "1; mode=block",
        x_content_type_options: str = "nosniff",
        x_frame_options: str = "DENY",
    ) -> None:
        super().__init__(app)
        self.force_https = force_https
        self.headers: dict[str, str] = {}

        if content_security_policy:
            self.headers["Content-Security-Policy"] = content_security_policy
        if strict_transport_security:
            self.headers["Strict-Transport-Security"] = strict_transport_security
        if referrer_policy:
            self.headers["Referrer-Policy"] = referrer_policy
        if x_xss_protection:
            self.headers["X-XSS-Protection"] = x_xss_protection
        if x_content_type_options or content_type_nosniff:
            self.headers["X-Content-Type-Options"] = x_content_type_options or "nosniff"
        if x_frame_options or frame_options:
            self.headers["X-Frame-Options"] = x_frame_options or frame_options

    async def dispatch(self, request, call_next):
        if self.force_https and request.url.scheme == "http":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url, status_code=307)

        response = await call_next(request)
        for key, value in self.headers.items():
            response.headers.setdefault(key, value)
        return response


__all__ = ["SecurityHeadersMiddleware"]
