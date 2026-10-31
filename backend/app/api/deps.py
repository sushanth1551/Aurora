"""FastAPI dependencies that assemble services from settings and a session."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.repositories.research_repository import ResearchRepository
from app.database.session import get_session
from app.research.research_service import ResearchService
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlPolicy
from app.tools.firecrawl_tool import FirecrawlTool
from app.tools.serpapi_tool import SerpApiTool

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_research_service(session: SessionDep) -> ResearchService:
    """Build a request-scoped research service with real adapters."""
    settings = get_settings()
    policy = UrlPolicy(
        dns_timeout=settings.research_dns_timeout_seconds,
        max_redirects=settings.research_max_redirects,
    )
    return ResearchService(
        search=SerpApiTool(settings),
        extractor=FirecrawlTool(settings),
        sources=SourceManager(policy),
        repository=ResearchRepository(session),
        settings=settings,
    )


ResearchServiceDep = Annotated[ResearchService, Depends(get_research_service)]
