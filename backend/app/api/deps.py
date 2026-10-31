"""FastAPI dependencies that assemble services from settings and a session."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.repositories.analysis_repository import AnalysisRepository
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.database.session import get_session
from app.intelligence.context_builder import ContextBuilder
from app.intelligence.followup import WebsiteFollowUpExecutor
from app.intelligence.service import IntelligenceAnalysisService
from app.research.cache import CachedMapper, CachedSearch, shared_cache
from app.research.intelligent_crawler import IntelligentCrawler
from app.research.research_service import ResearchService
from app.research.robots import RobotsChecker
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlPolicy
from app.research.website_discovery import WebsiteDiscovery
from app.research.website_intelligence_service import WebsiteIntelligenceService
from app.research.website_mapper import WebsiteMapper
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


def get_intelligence_service(session: SessionDep) -> WebsiteIntelligenceService:
    """Build a request-scoped website intelligence service with real adapters."""
    settings = get_settings()
    policy = UrlPolicy(
        dns_timeout=settings.research_dns_timeout_seconds,
        max_redirects=settings.research_max_redirects,
    )
    sources = SourceManager(policy)
    firecrawl = FirecrawlTool(settings)
    robots = RobotsChecker(policy) if settings.website_research_respect_robots else None
    ttl = settings.website_research_cache_ttl_seconds
    size = settings.website_research_cache_max_entries
    search = CachedSearch(SerpApiTool(settings), shared_cache("search", ttl, size))
    mapper = CachedMapper(firecrawl, shared_cache("map", ttl, size))
    return WebsiteIntelligenceService(
        discovery=WebsiteDiscovery(search, sources, settings),
        mapper=WebsiteMapper(mapper, sources, settings=settings),
        crawler=IntelligentCrawler(firecrawl, sources, settings, robots),
        sources=sources,
        repository=IntelligenceRepository(session),
        settings=settings,
    )


IntelligenceServiceDep = Annotated[
    WebsiteIntelligenceService, Depends(get_intelligence_service)
]


def get_analysis_service(session: SessionDep) -> IntelligenceAnalysisService:
    """Build a request-scoped analysis service. The LLM provider is chosen per
    request by ``build_llm_provider``; follow-up research reuses Module 3."""
    settings = get_settings()
    return IntelligenceAnalysisService(
        repository=AnalysisRepository(session),
        builder=ContextBuilder(
            ResearchRepository(session),
            IntelligenceRepository(session),
            settings=settings,
        ),
        executor=WebsiteFollowUpExecutor(get_intelligence_service(session)),
        settings=settings,
    )


AnalysisServiceDep = Annotated[
    IntelligenceAnalysisService, Depends(get_analysis_service)
]
