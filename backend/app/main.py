"""FastAPI application factory and ASGI entry point.

Importing this module or starting the app never connects to databases or
external APIs and requires no credentials.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_context import REQUEST_ID_HEADER, new_request_id, request_id_var
from app.database.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure logging; close the database pool (if opened) on shutdown."""
    configure_logging(get_settings().log_level)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Build and configure the AURORA API application."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=settings.app_description,
        debug=settings.debug,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def correlation_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a request id to logs and the response (safe client ids only)."""
        request_id = new_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @application.get("/", tags=["root"], summary="Application information")
    async def root() -> dict[str, str]:
        """Describe the running application."""
        return {
            "application": settings.app_name,
            "version": settings.app_version,
            "description": settings.app_description,
            "status": "running",
        }

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    register_exception_handlers(application)
    return application


app = create_app()
