"""Central API router aggregating feature routes."""

from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.research import router as research_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
# Included first so the fixed paths (/websites, /intelligence) win over /{job_id}.
api_router.include_router(
    intelligence_router, prefix="/research", tags=["website-intelligence"]
)
api_router.include_router(research_router, prefix="/research", tags=["research"])
