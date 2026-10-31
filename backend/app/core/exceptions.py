"""Shared application exceptions and centralized error response handling.

Responses use one stable shape: ``{"error": {"code": ..., "message": ...}}``.
Messages must be safe to show to clients: no stack traces, no secrets.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AuroraException(Exception):
    """Base class for expected application-level errors."""

    status_code = 500
    code = "aurora_error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "An application error occurred."
        super().__init__(self.message)


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


class ResourceNotFoundException(AuroraException):
    """The requested resource does not exist."""

    status_code = 404
    code = "resource_not_found"


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(application: FastAPI) -> None:
    """Register safe, consistent handlers for application and unexpected errors."""

    @application.exception_handler(AuroraException)
    async def handle_aurora_exception(_: Request, exc: AuroraException) -> JSONResponse:
        logger.warning("%s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.code, exc.message)
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_server_error", "An unexpected error occurred."
            ),
        )
