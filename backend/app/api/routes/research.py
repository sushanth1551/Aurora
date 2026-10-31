"""Research endpoints.

Research runs **synchronously**: ``POST /research`` blocks until the job has
finished (or ``RESEARCH_TIMEOUT_SECONDS`` elapses) and returns the persisted
result. Jobs are never "running" after the response; there is no background
queue yet.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import ResearchServiceDep
from app.research.research_service import INFRASTRUCTURE_CATEGORIES
from app.schemas.research import JobStatus, ResearchRequest, ResearchResponse

router = APIRouter()

_ERROR_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": "Research job not found"},
    503: {"description": "A required service (provider, database) is unavailable"},
}


@router.post(
    "",
    response_model=ResearchResponse,
    responses=_ERROR_RESPONSES,
    summary="Run a research job (synchronous)",
)
async def submit_research(
    request: ResearchRequest, response: Response, service: ResearchServiceDep
) -> ResearchResponse:
    """Search the web, validate URLs, extract pages, persist, and return results.

    * 200: ``completed``, ``partially_completed``, or a ``failed`` job whose
      cause is not an outage (e.g. every page failed); inspect ``status``.
    * 503: the job failed because SerpApi/Firecrawl credentials, availability or
      rate limits, or the database, prevented it. The body still identifies
      the job when one was stored.
    """
    result = await service.run(request)
    if (
        result.status == JobStatus.FAILED
        and result.failure is not None
        and result.failure.category in INFRASTRUCTURE_CATEGORIES
    ):
        response.status_code = 503
    return result


@router.get(
    "/{job_id}",
    response_model=ResearchResponse,
    responses=_ERROR_RESPONSES,
    summary="Retrieve a stored research job",
)
async def get_research(
    job_id: UUID,
    service: ResearchServiceDep,
    include_content: Annotated[
        bool, Query(description="Include extracted Markdown in sources")
    ] = True,
) -> ResearchResponse:
    """Return a persisted job with its search results and sources."""
    return await service.get(job_id, include_content=include_content)
