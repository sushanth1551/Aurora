"""FastAPI application factory and ASGI entry point.

Importing this module or starting the app never connects to databases or
external APIs and requires no credentials.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure process-level logging only; no external services are opened."""
    configure_logging(get_settings().log_level)
    yield


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
