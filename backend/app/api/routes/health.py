"""Liveness endpoint for process and deployment checks."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter()


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
