"""Domain exceptions and centralised exception -> HTTP problem mapping (RFC 7807 style)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger, request_id_var

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class PolicyViolationError(AppError):
    status_code = 403
    code = "policy_violation"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class OverloadedError(AppError):
    """Raised when bounded queues are full (backpressure)."""

    status_code = 503
    code = "overloaded"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"


class CircuitOpenError(UpstreamError):
    status_code = 503
    code = "circuit_open"


class FeatureDisabledError(AppError):
    status_code = 404
    code = "feature_disabled"


def _problem(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    headers = {}
    if status in (429, 503):
        headers["Retry-After"] = "5"
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "request_id": request_id_var.get(),
        },
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _problem(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return _problem(422, "validation_failed", "Request validation failed", {"errors": errs})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return _problem(500, "internal_error", "An internal error occurred")
