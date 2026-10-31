"""Schemas for research requests and responses."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ResearchDepth(StrEnum):
    """How thorough a future research run should be."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ResearchStatus(StrEnum):
    """Lifecycle states reported to clients.

    Only states that actually exist are listed; extend when the workflow does.
    """

    NOT_IMPLEMENTED = "not_implemented"


class ResearchRequest(BaseModel):
    """Input describing a research topic."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str = Field(min_length=2, max_length=500, description="Topic or company")
    description: str | None = Field(
        default=None, max_length=2000, description="Optional context or goals"
    )
    research_depth: ResearchDepth = Field(
        default=ResearchDepth.STANDARD, description="Requested thoroughness"
    )
    max_sources: int = Field(
        default=10, ge=1, le=50, description="Upper bound on sources to consult"
    )


class ResearchResponse(BaseModel):
    """Response for a validated research request."""

    request_id: UUID = Field(default_factory=uuid4, description="Request identifier")
    topic: str = Field(description="Validated topic")
    status: ResearchStatus = Field(description="Current processing status")
    message: str = Field(description="Human-readable explanation of the status")
    is_placeholder: bool = Field(description="True when no real work was performed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceReference(BaseModel):
    """Provenance for a retrieved source; retained for every derived claim."""

    url: HttpUrl = Field(description="Source location")
    title: str | None = Field(default=None, max_length=500)
    retrieved_at: datetime | None = Field(
        default=None, description="When the content was retrieved (UTC)"
    )
