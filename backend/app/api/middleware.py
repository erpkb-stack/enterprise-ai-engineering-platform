"""ASGI middleware: request id, latency metrics, rate limiting, security headers.

Implemented as pure ASGI (not BaseHTTPMiddleware) to avoid per-request task overhead and to keep
streaming/WebSocket traffic untouched.
"""

from __future__ import annotations

import json
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import get_logger, request_id_var
from app.core.redis import kv_incr
from app.observability import metrics

log = get_logger("http")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


def synthetic_token() -> str:
    import hashlib

    return hashlib.sha256(("synthetic:" + get_settings().jwt_secret.get_secret_value()).encode()).hexdigest()[:32]


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.settings = get_settings()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id") or uuid.uuid4().hex
        request_id_var.set(rid[:64])
        # synthetic (load-test) traffic is tagged with a server-derived token so clients cannot spoof it
        synthetic = headers.get("x-aiecc-synthetic") == synthetic_token()
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        t0 = time.perf_counter()

        # --- rate limiting (fixed window per client IP; stricter for auth endpoints) ---------
        if path.startswith(self.settings.api_prefix) and not synthetic:
            client = (scope.get("client") or ("unknown", 0))[0]
            is_auth = path.startswith(f"{self.settings.api_prefix}/auth/login")
            limit = self.settings.rate_limit_auth_per_minute if is_auth else self.settings.rate_limit_per_minute
            window = int(time.time() // 60)
            n = await kv_incr(f"rl:{'auth' if is_auth else 'api'}:{client}:{window}", 70)
            if n > limit:
                body = json.dumps({"error": {"code": "rate_limited", "message": "Rate limit exceeded",
                                             "details": {"limit_per_minute": limit}}, "request_id": rid}).encode()
                await send({"type": "http.response.start", "status": 429,
                            "headers": [(b"content-type", b"application/json"), (b"retry-after", b"60"),
                                        (b"x-request-id", rid.encode()), *SECURITY_HEADERS]})
                await send({"type": "http.response.body", "body": body})
                metrics.record_http(method, "rate_limited", 429, (time.perf_counter() - t0) * 1000, synthetic)
                return

        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                hs = list(message.get("headers", []))
                hs.append((b"x-request-id", rid.encode()))
                existing = {k.lower() for k, _ in hs}
                hs.extend(h for h in SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = hs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency = (time.perf_counter() - t0) * 1000
            route = _route_template(scope)
            if route not in ("/health", "/metrics", "/ready"):
                metrics.record_http(method, route, status["code"], latency, synthetic)
            if latency > 2000:
                log.warning("slow_request %s %s %.0fms", method, route, latency)
