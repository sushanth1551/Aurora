"""Intelligence synthesis endpoints (Module 4), mounted under ``/intelligence``.

``POST /analyze`` runs **synchronously** (bounded by
``INTELLIGENCE_MAX_DURATION_SECONDS``); progress is committed at each stage, so
the ``GET`` endpoints can be polled from another client while it runs.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.api.deps import AnalysisServiceDep
from app.intelligence.models import IntelligenceReport
from app.schemas.analysis import (
    AnalysisResponse,
    AnalyzeRequest,
    QualityResponse,
    UsageResponse,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Research job, analysis or report not found"},
    503: {"description": "LLM provider or database not configured/unavailable"},
}
AnalysisIdQuery = Annotated[
    UUID | None, Query(description="A specific analysis (default: the latest)")
]


@router.post(
    "/analyze",
    response_model=AnalysisResponse,
    responses=_ERRORS,
    summary="Analyse a stored research job (synchronous)",
)
async def analyze(
    request: AnalyzeRequest, service: AnalysisServiceDep
) -> AnalysisResponse:
    """Build context, reason, synthesise, report and evaluate; persists everything."""
    return await service.analyze(request)


@router.get(
    "/{job_id}",
    response_model=AnalysisResponse,
    responses=_ERRORS,
    summary="Latest analysis for a research job",
)
async def get_analysis(job_id: UUID, service: AnalysisServiceDep) -> AnalysisResponse:
    return await service.latest(job_id)


@router.get(
    "/{job_id}/report",
    response_model=IntelligenceReport,
    responses={**_ERRORS, 200: {"content": {"text/markdown": {}}}},
    summary="Analysis report as JSON or Markdown",
)
async def get_report(
    job_id: UUID,
    service: AnalysisServiceDep,
    analysis_id: AnalysisIdQuery = None,
    format: Annotated[Literal["json", "markdown"], Query()] = "json",
) -> IntelligenceReport | PlainTextResponse:
    report, markdown = await service.report(job_id, analysis_id)
    if format == "markdown":
        return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")
    return report


@router.get(
    "/{job_id}/usage",
    response_model=UsageResponse,
    responses=_ERRORS,
    summary="LLM token usage and estimated cost",
)
async def get_usage(
    job_id: UUID, service: AnalysisServiceDep, analysis_id: AnalysisIdQuery = None
) -> UsageResponse:
    return await service.usage(job_id, analysis_id)


@router.get(
    "/{job_id}/quality",
    response_model=QualityResponse,
    responses=_ERRORS,
    summary="Report quality evaluation",
)
async def get_quality(
    job_id: UUID, service: AnalysisServiceDep, analysis_id: AnalysisIdQuery = None
) -> QualityResponse:
    return await service.quality(job_id, analysis_id)
