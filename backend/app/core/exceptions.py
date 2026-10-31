"""Shared application exceptions and centralized error response handling.

Responses use one stable shape: ``{"error": {"code": ..., "message": ...}}``.
Messages must be safe to show to clients: no stack traces, no secrets.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

MAX_VALIDATION_DETAILS = 20


class AuroraException(Exception):
    """Base class for expected application-level errors."""

    status_code = 500
    code = "aurora_error"

    def __init__(
        self, message: str | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.message = message or "An application error occurred."
        self.headers = headers
        super().__init__(self.message)


class AuthenticationException(AuroraException):
    """The request has no valid API token."""

    status_code = 401
    code = "authentication_required"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "A valid API token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class RateLimitException(AuroraException):
    """The client sent too many requests (or failed authentications) recently."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(
            message, headers={"Retry-After": str(max(1, int(retry_after) + 1))}
        )


class ConfigurationException(AuroraException):
    """A required setting or credential is missing or invalid."""

    status_code = 503
    code = "configuration_error"


class IntegrationException(AuroraException):
    """An external integration is unavailable, failed, or not implemented."""

    status_code = 502
    code = "integration_error"


class ResearchException(AuroraException):
    """A research request or workflow could not be completed."""

    status_code = 422
    code = "research_error"


class PersistenceException(AuroraException):
    """The database is unavailable or a persistence operation failed."""

    status_code = 503
    code = "persistence_error"


class ConflictException(AuroraException):
    """The request conflicts with the resource's current state."""

    status_code = 409
    code = "conflict"


class ResourceNotFoundException(AuroraException):
    """The requested resource does not exist."""

    status_code = 404
    code = "resource_not_found"


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    """Location, message and type of each error; the rejected input is never echoed."""
    details = []
    for error in exc.errors()[:MAX_VALIDATION_DETAILS]:
        loc = ".".join(str(part) for part in error.get("loc", ()))
        details.append(
            {
                "location": loc,
                "message": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return details


def register_exception_handlers(application: FastAPI) -> None:
    """Register safe, consistent handlers for application and unexpected errors."""

    @application.exception_handler(AuroraException)
    async def handle_aurora_exception(_: Request, exc: AuroraException) -> JSONResponse:
        logger.warning("%s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = _validation_details(exc)
        first = details[0] if details else None
        message = "Request validation failed."
        if first:
            message = (
                f"Request validation failed: {first['location']}: {first['message']}"
            )
        content: dict[str, Any] = _error_body("validation_error", message)
        content["error"]["details"] = details
        return JSONResponse(status_code=422, content=content)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_server_error", "An unexpected error occurred."
            ),
        )
