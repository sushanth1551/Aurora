"""FastAPI dependencies that assemble services from settings and a session."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.repositories.analysis_repository import AnalysisRepository
from app.database.repositories.graph_sync_repository import GraphSyncRepository
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.keyword_repository import KeywordRepository
from app.database.repositories.research_repository import ResearchRepository
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.database.session import get_session
from app.graph.graph_exceptions import GraphNotConfiguredError
from app.graph.graph_query_service import GraphQueryService
from app.graph.graph_repository import Neo4jGraphRepository
from app.graph.graph_sync_service import GraphSyncService
from app.graph.neo4j_client import Neo4jClient, get_graph_client
from app.intelligence.context_builder import ContextBuilder
from app.intelligence.followup import WebsiteFollowUpExecutor
from app.intelligence.service import IntelligenceAnalysisService
from app.monitoring.factory import build_gsc_service
from app.monitoring.oauth import OAuthFlows
from app.monitoring.service import GscService
from app.research.cache import CachedMapper, CachedSearch, shared_cache
from app.research.intelligent_crawler import IntelligentCrawler
from app.research.research_service import ResearchService
from app.research.robots import RobotsChecker
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlPolicy
from app.research.website_discovery import WebsiteDiscovery
from app.research.website_intelligence_service import WebsiteIntelligenceService
from app.research.website_mapper import WebsiteMapper
from app.seo.intelligence.factory import (
    build_seo_agent_service,
    build_skill_improvement,
)
from app.seo.intelligence.service import SeoAgentService
from app.seo.keywords.service import KeywordResearchService
from app.seo.skills.improvement import SkillImprovementService
from app.seo.technical.service import TechnicalSeoService
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


# -- Module 5: knowledge graph ---------------------------------------------------------


def get_graph_client_dep() -> Neo4jClient:
    """Shared Neo4j client (lazy: the driver connects on first use)."""
    return get_graph_client()


GraphClientDep = Annotated[Neo4jClient, Depends(get_graph_client_dep)]


def get_graph_repository(client: GraphClientDep) -> Neo4jGraphRepository:
    """Neo4j-backed repository; raises 503 when Neo4j is not configured."""
    if not client.is_configured:
        raise GraphNotConfiguredError(
            "Neo4j is not configured; set NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD."
        )
    return Neo4jGraphRepository(client)


GraphRepositoryDep = Annotated[Neo4jGraphRepository, Depends(get_graph_repository)]


def get_graph_query_service(repo: GraphRepositoryDep) -> GraphQueryService:
    return GraphQueryService(repo, get_settings())


GraphQueryDep = Annotated[GraphQueryService, Depends(get_graph_query_service)]


def get_graph_sync_repository(session: SessionDep) -> GraphSyncRepository:
    return GraphSyncRepository(session)


GraphSyncRepoDep = Annotated[GraphSyncRepository, Depends(get_graph_sync_repository)]


def get_graph_sync_service(
    session: SessionDep, client: GraphClientDep
) -> GraphSyncService:
    """Sync service; the graph is optional so dry runs work without Neo4j."""
    settings = get_settings()
    return GraphSyncService(
        research_repo=ResearchRepository(session),
        intelligence_repo=IntelligenceRepository(session),
        sync_repo=GraphSyncRepository(session),
        graph=Neo4jGraphRepository(client) if client.is_configured else None,
        settings=settings,
    )


GraphSyncDep = Annotated[GraphSyncService, Depends(get_graph_sync_service)]


def get_seo_audit_service(session: SessionDep) -> TechnicalSeoService:
    """Technical SEO audit service; Firecrawl is required only when an audit runs."""
    settings = get_settings()
    policy = UrlPolicy(
        dns_timeout=settings.research_dns_timeout_seconds,
        max_redirects=settings.research_max_redirects,
    )
    return TechnicalSeoService(
        repository=SeoAuditRepository(session),
        settings=settings,
        policy=policy,
        scraper=FirecrawlTool(settings),
    )


SeoAuditServiceDep = Annotated[TechnicalSeoService, Depends(get_seo_audit_service)]


def get_keyword_service(session: SessionDep) -> KeywordResearchService:
    """Keyword research service; SerpApi is required only when a run starts."""
    settings = get_settings()
    policy = UrlPolicy(
        dns_timeout=settings.research_dns_timeout_seconds,
        max_redirects=settings.research_max_redirects,
    )
    return KeywordResearchService(
        repository=KeywordRepository(session),
        audit_repository=SeoAuditRepository(session),
        settings=settings,
        policy=policy,
        serp=SerpApiTool(settings),
    )


KeywordServiceDep = Annotated[KeywordResearchService, Depends(get_keyword_service)]


def get_gsc_service(request: Request, session: SessionDep) -> GscService:
    """Search Console service; Google is contacted only by endpoints that say so."""
    settings = get_settings()
    flows = getattr(request.app.state, "gsc_oauth_flows", None)
    if flows is None:  # app built without create_app()
        flows = OAuthFlows(settings.gsc_oauth_state_ttl_seconds)
        request.app.state.gsc_oauth_flows = flows
    return build_gsc_service(session, settings, flows)


GscServiceDep = Annotated[GscService, Depends(get_gsc_service)]


def get_seo_agent_service() -> SeoAgentService:
    """SEO Intelligence service over the file-backed knowledge base (no network)."""
    return build_seo_agent_service(get_settings())


SeoAgentServiceDep = Annotated[SeoAgentService, Depends(get_seo_agent_service)]


def get_skill_improvement() -> SkillImprovementService:
    """Skill proposals are listed through the API; decisions stay in the CLI."""
    return build_skill_improvement(get_settings())


SkillImprovementDep = Annotated[SkillImprovementService, Depends(get_skill_improvement)]
