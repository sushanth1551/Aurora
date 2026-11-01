"""Central API router aggregating feature routes.

Health routes are public; every other route requires the local API token
(``app.api.security.require_api_token``).
"""

from fastapi import APIRouter, Depends

from app.api.routes.analysis import router as analysis_router
from app.api.routes.auth import router as auth_router
from app.api.routes.graph import router as graph_router
from app.api.routes.gsc import public_router as gsc_public_router
from app.api.routes.gsc import router as gsc_router
from app.api.routes.health import router as health_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.keywords import router as keywords_router
from app.api.routes.research import router as research_router
from app.api.routes.seo import router as seo_router
from app.api.security import require_api_token

protected_router = APIRouter(dependencies=[Depends(require_api_token)])
protected_router.include_router(auth_router, prefix="/auth", tags=["auth"])
# Included first so the fixed paths (/websites, /intelligence) win over /{job_id}.
protected_router.include_router(
    intelligence_router, prefix="/research", tags=["website-intelligence"]
)
protected_router.include_router(research_router, prefix="/research", tags=["research"])
protected_router.include_router(
    analysis_router, prefix="/intelligence", tags=["intelligence-synthesis"]
)
protected_router.include_router(graph_router, prefix="/graph", tags=["knowledge-graph"])
protected_router.include_router(seo_router, prefix="/seo", tags=["technical-seo"])
protected_router.include_router(gsc_router, prefix="/gsc", tags=["search-console"])
protected_router.include_router(
    keywords_router, prefix="/seo/keywords", tags=["keyword-research"]
)

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
# The OAuth callback is public: Google redirects the browser there, so no bearer token
# is possible. It is guarded by the single-use state from POST /gsc/oauth/start.
api_router.include_router(gsc_public_router, prefix="/gsc", tags=["search-console"])
api_router.include_router(protected_router)
