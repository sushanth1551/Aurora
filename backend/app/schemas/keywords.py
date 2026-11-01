"""API contracts for keyword research (Phase 3)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    """Plan a keyword research run. No SerpApi request is made until it is approved and run."""

    model_config = ConfigDict(extra="forbid")

    site_url: str = Field(
        min_length=4, max_length=2048, description="The website being researched"
    )
    seeds: list[str] = Field(default_factory=list, max_length=25)
    country: str | None = Field(
        default=None, pattern=r"^[A-Za-z]{2}$", description="gl, e.g. us"
    )
    language: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z]{2}(-[A-Za-z]{2,4})?$",
        description="hl, e.g. en",
    )
    device: Literal["desktop", "mobile", "tablet"] | None = None
    location: str | None = Field(default=None, max_length=200)
    max_queries: int | None = Field(
        default=None, ge=1, description="Cap: SEO_KEYWORD_MAX_QUERIES"
    )
    include_expansions: bool = Field(
        default=False,
        description="Also search template variations of the seeds (uses more searches)",
    )


class RunApproval(BaseModel):
    """Explicit approval of the plan's search ceiling."""

    model_config = ConfigDict(extra="forbid")

    approve: Literal[True]
    max_searches: int = Field(
        ge=1, description="At most the plan's serpapi_searches_max"
    )


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_url: str
    host: str
    status: str
    params: dict[str, Any]
    limits: dict[str, Any]
    estimate: dict[str, Any]
    approval: dict[str, Any] | None
    usage: dict[str, Any]
    summary: dict[str, Any]
    gap_status: str | None
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunList(BaseModel):
    items: list[RunView]
    total: int


class CandidateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    keyword: str
    normalized: str
    source: str
    source_detail: str | None
    intent: str | None
    intent_basis: str | None
    cluster: str | None
    searched: bool
    snapshot_id: UUID | None
    metrics: dict[str, Any]


class CandidateList(BaseModel):
    items: list[CandidateView]
    total: int


class OpportunityView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    candidate_id: UUID
    keyword: str
    rank: int
    action: str
    intent: str | None
    intent_basis: str | None
    cluster: str | None
    impact: str
    confidence: str
    effort: str
    risk: str
    rationale: list[str]
    serp: dict[str, Any]
    gap_terms: list[str]
    questions: list[str]
    metrics: dict[str, Any]
    basis: str
    selected: bool
    selected_at: datetime | None


class OpportunityList(BaseModel):
    items: list[OpportunityView]
    total: int


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: bool


class SelectedOpportunity(BaseModel):
    """What article generation (a later phase) receives; no article is generated here."""

    opportunity_id: UUID
    run_id: UUID
    host: str
    keyword: str
    intent: str | None
    action: str
    cluster: str | None
    gap_terms: list[str]
    questions: list[str]
    serp_snapshot_id: str | None
    selected_at: datetime | None
    run_created_at: datetime


class SelectedList(BaseModel):
    items: list[SelectedOpportunity]


class CompetitorList(BaseModel):
    basis: str = "SERP competitors: domains observed in the top 10 results of the researched queries"
    items: list[dict[str, Any]]


class GapList(BaseModel):
    status: str
    note: str
    items: list[dict[str, Any]]


class SerpSnapshotView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    country: str | None
    language: str | None
    device: str | None
    location: str | None
    provider: str
    provider_search_id: str | None
    observed_at: datetime
    organic: list[dict[str, Any]]
    features: dict[str, Any]
