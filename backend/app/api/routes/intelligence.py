"""Website intelligence endpoints (Module 3), mounted under ``/research``.

All work is **synchronous**: a request returns when its step has finished (or the
configured deadline passed). For large jobs prefer the staged endpoints
(discover, map, crawl) over the all-in-one ``/intelligence`` call, and poll
``GET /{job_id}/progress`` to inspect a stored job.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.api.deps import IntelligenceServiceDep
from app.schemas.intelligence import (
    ClaimsPage,
    CrawlWebsiteRequest,
    CrawlWebsiteResponse,
    DiscoverWebsitesRequest,
    EntitiesPage,
    EntityType,
    GeneratedReport,
    IntelligenceRequest,
    IntelligenceResponse,
    MapWebsiteRequest,
    MapWebsiteResponse,
    ReportType,
    WebsiteDiscoveryResponse,
    WebsitesPage,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Job, website or report not found"},
    502: {"description": "An external provider failed"},
    503: {"description": "A required service (provider, database) is unavailable"},
}
Limit = Annotated[int, Query(ge=1, le=200, description="Page size")]
Offset = Annotated[int, Query(ge=0, description="Items to skip")]


@router.post(
    "/websites",
    response_model=WebsiteDiscoveryResponse,
    responses=_ERRORS,
    summary="Discover relevant websites (synchronous)",
)
async def discover_websites(
    request: DiscoverWebsitesRequest, service: IntelligenceServiceDep
) -> WebsiteDiscoveryResponse:
    """Search, validate and rank candidate websites; stores a new job."""
    return await service.discover(request)


@router.post(
    "/websites/{website_id}/map",
    response_model=MapWebsiteResponse,
    responses=_ERRORS,
    summary="Map the pages of a discovered website",
)
async def map_website(
    website_id: UUID, request: MapWebsiteRequest, service: IntelligenceServiceDep
) -> MapWebsiteResponse:
    """List, classify and rank the website's URLs (no page content is fetched)."""
    return await service.map_website(website_id, request)


@router.post(
    "/websites/{website_id}/crawl",
    response_model=CrawlWebsiteResponse,
    responses=_ERRORS,
    summary="Crawl the selected pages of a mapped website",
)
async def crawl_website(
    website_id: UUID, request: CrawlWebsiteRequest, service: IntelligenceServiceDep
) -> CrawlWebsiteResponse:
    """Scrape the most relevant mapped pages, then extract and validate facts."""
    return await service.crawl_website(website_id, request)


@router.post(
    "/intelligence",
    response_model=IntelligenceResponse,
    responses=_ERRORS,
    summary="Run the complete website intelligence workflow (synchronous)",
)
async def run_intelligence(
    request: IntelligenceRequest, service: IntelligenceServiceDep
) -> IntelligenceResponse:
    """Discover, map, crawl, extract, validate and report; blocks until done.

    The response is a summary; retrieve details from the ``/{job_id}/...`` routes.
    """
    return await service.run(request)


@router.get(
    "/{job_id}/progress",
    response_model=IntelligenceResponse,
    responses=_ERRORS,
    summary="Job status, stage timings and counts",
)
async def get_progress(
    job_id: UUID, service: IntelligenceServiceDep
) -> IntelligenceResponse:
    """Return the stored state of an intelligence job."""
    return await service.get_progress(job_id)


@router.get(
    "/{job_id}/websites",
    response_model=WebsitesPage,
    responses=_ERRORS,
    summary="Websites discovered for a job",
)
async def list_websites(
    job_id: UUID, service: IntelligenceServiceDep, limit: Limit = 50, offset: Offset = 0
) -> WebsitesPage:
    """Paginated websites, best relevance first."""
    return await service.get_websites(job_id, limit, offset)


@router.get(
    "/{job_id}/entities",
    response_model=EntitiesPage,
    responses=_ERRORS,
    summary="Entities extracted for a job",
)
async def list_entities(
    job_id: UUID,
    service: IntelligenceServiceDep,
    entity_type: EntityType | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> EntitiesPage:
    """Paginated resolved entities, optionally filtered by type."""
    return await service.get_entities(job_id, entity_type, limit, offset)


@router.get(
    "/{job_id}/claims",
    response_model=ClaimsPage,
    responses=_ERRORS,
    summary="Evidence-backed claims for a job",
)
async def list_claims(
    job_id: UUID,
    service: IntelligenceServiceDep,
    status: Annotated[
        Literal[
            "supported",
            "partially_supported",
            "conflicting",
            "unsupported",
            "unverified",
        ]
        | None,
        Query(description="Filter by verification status"),
    ] = None,
    predicate: Annotated[str | None, Query(max_length=64)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ClaimsPage:
    """Paginated claims, each with its source URL and evidence excerpt."""
    return await service.get_claims(job_id, status, predicate, limit, offset)


@router.get(
    "/{job_id}/report",
    response_model=GeneratedReport,
    responses={**_ERRORS, 200: {"content": {"text/markdown": {}}}},
    summary="The generated research report (JSON or Markdown)",
)
async def get_report(
    job_id: UUID,
    service: IntelligenceServiceDep,
    report_type: ReportType | None = None,
    format: Literal["json", "markdown"] = "json",  # noqa: A002 - public query name
) -> GeneratedReport | PlainTextResponse:
    """Return the report as structured JSON, or rendered Markdown."""
    report = await service.get_report(job_id, report_type)
    if format == "markdown":
        return PlainTextResponse(report.markdown, media_type="text/markdown")
    return report
