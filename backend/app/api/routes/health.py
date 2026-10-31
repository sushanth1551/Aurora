"""Liveness and readiness endpoints (no API token required).

``/health`` contacts nothing. ``/health/ready`` checks the database, migration
state and graph status with timeouts and returns status words only; it is rate
limited because it touches dependencies.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.api.security import limit_public_requests
from app.core.config import get_settings
from app.core.readiness import EngineFactory, ReadinessReport, check_readiness
from app.database.session import get_engine
from app.graph.neo4j_client import Neo4jClient, get_graph_client

router = APIRouter()


def get_engine_factory() -> EngineFactory:
    """The engine used by readiness checks (overridable in tests)."""
    return get_engine


class HealthResponse(BaseModel):
    """Liveness payload; does not reflect database or provider status."""

    status: str
    application: str
    version: str


@router.get("/health", response_model=HealthResponse, summary="Check API liveness")
async def health_check() -> HealthResponse:
    """Report that the API process is responsive; no dependency is contacted."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        application=settings.app_name,
        version=settings.app_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessReport,
    summary="Check readiness (database, migrations, graph)",
    responses={503: {"description": "Database unavailable or not configured"}},
    dependencies=[Depends(limit_public_requests)],
)
async def readiness_check(
    response: Response,
    graph_client: Annotated[Neo4jClient, Depends(get_graph_client)],
    engine_factory: Annotated[EngineFactory, Depends(get_engine_factory)],
) -> ReadinessReport:
    """``ready`` / ``degraded`` answer 200; ``not_ready`` answers 503."""
    report = await check_readiness(engine_factory, graph_client)
    if report.status == "not_ready":
        response.status_code = 503
    return report
