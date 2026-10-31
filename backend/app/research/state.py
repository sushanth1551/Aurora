"""Typed workflow state for the research pipeline.

``ResearchState`` is a ``TypedDict`` so a future LangGraph ``StateGraph`` can
use it directly. List fields that several nodes append to use the
``operator.add`` reducer. It carries no credentials or client objects.
"""

import operator
from datetime import datetime
from typing import Annotated, TypedDict
from uuid import UUID

from app.schemas.research import (
    ErrorDetail,
    ExtractedPage,
    JobStatus,
    ResearchRequest,
    ResearchSource,
    SearchResult,
    StageSummary,
)


class ResearchState(TypedDict, total=False):
    """State passed between research pipeline stages."""

    job_id: UUID
    query: str
    request: ResearchRequest
    status: JobStatus
    current_stage: str
    search_results: list[SearchResult]
    validated_urls: list[str]
    extracted_pages: list[ExtractedPage]
    sources: list[ResearchSource]
    stages: Annotated[list[StageSummary], operator.add]
    errors: Annotated[list[ErrorDetail], operator.add]
    created_at: datetime
    started_at: datetime
    completed_at: datetime
