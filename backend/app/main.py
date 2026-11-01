"""FastAPI application factory and ASGI entry point.

Importing this module or starting the app never connects to databases or
external APIs and requires no credentials.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimits
from app.core.request_context import REQUEST_ID_HEADER, new_request_id, request_id_var
from app.database.session import create_session_factory, dispose_engine
from app.graph.neo4j_client import close_graph_client
from app.monitoring.factory import build_gsc_service
from app.monitoring.oauth import OAuthFlows
from app.monitoring.scheduler import GscScheduler


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Configure logging; close the database pool and graph driver (if opened).

    The Search Console scheduler starts only when ``GSC_SCHEDULER_ENABLED=true``
    (default false); it is stopped here on shutdown.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    if settings.gsc_scheduler_enabled:
        scheduler = GscScheduler(
            create_session_factory(),
            settings,
            lambda session: build_gsc_service(
                session, settings, application.state.gsc_oauth_flows
            ),
        )
        scheduler_task = asyncio.create_task(scheduler.run_forever(stop))
    yield
    stop.set()
    if scheduler_task is not None:
        await scheduler_task
    await dispose_engine()
    await close_graph_client()


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
    application.state.rate_limits = RateLimits.from_settings(settings)
    application.state.gsc_oauth_flows = OAuthFlows(settings.gsc_oauth_state_ttl_seconds)

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
