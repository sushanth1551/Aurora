"""API contracts for the intelligence synthesis endpoints (Module 4)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.intelligence.models import (
    AnalysisStatus,
    AnalysisType,
    QualityStatus,
    ReportQualityMetrics,
)

_MODEL_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


class ResearchConfiguration(BaseModel):
    """Optional hints for follow-up research (never URLs or prompts)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    language: str | None = Field(
        default=None, pattern=r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{2,8})?$"
    )


class AnalyzeRequest(BaseModel):
    """Run an analysis over an existing research job.

    Limits can only be *lowered* here: effective values are the minimum of the
    request and the server configuration. There is deliberately no field for
    system prompts, URLs or budgets.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    research_job_id: UUID
    research_objective: str = Field(min_length=10, max_length=2000)
    analysis_type: AnalysisType = AnalysisType.DETAILED_REPORT
    preferred_provider: Literal["openai", "anthropic", "groq", "mock"] | None = None
    preferred_model: str | None = Field(default=None, pattern=_MODEL_ID)
    maximum_iterations: int | None = Field(default=None, ge=1, le=10)
    maximum_sources: int | None = Field(default=None, ge=1, le=500)
    output_format: Literal["json", "markdown", "both"] = "both"
    allow_follow_up_research: bool = False
    research_configuration: ResearchConfiguration | None = None

    @field_validator("research_objective")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("objective contains control characters")
        return value


class AnalysisProgress(BaseModel):
    stage: str = "pending"
    iterations: int = 0
    follow_up_queries: int = 0
    evidence_items: int = 0
    sources: int = 0
    #: Effective evidence-token budget (provider-specific) and what fit in it.
    context_token_budget: int | None = None
    context_budget_source: str | None = None
    context_tokens: int = 0
    evidence_considered: int = 0
    evidence_trimmed: bool = False
    llm_calls: int = 0
    tokens_used: int = 0
    estimated_cost: float | None = None
    cost_complete: bool = True
    stop_reason: str | None = None
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    analysis_id: UUID
    research_job_id: UUID
    research_objective: str
    analysis_type: AnalysisType
    status: AnalysisStatus
    provider: str
    model: str
    report_status: QualityStatus | None = None
    report_available: bool = False
    progress: AnalysisProgress = Field(default_factory=AnalysisProgress)
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    other_analysis_ids: list[UUID] = Field(default_factory=list)


class UsageEntry(BaseModel):
    operation: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    duration_seconds: float = 0.0
    estimated_cost: float | None = None
    cost_available: bool = False
    success: bool = True
    error_category: str | None = None
    fallback_from: str | None = None
    fallback_reason: str | None = None
    requested_at: datetime


class UsageResponse(BaseModel):
    analysis_id: UUID
    research_job_id: UUID
    requests: int
    total_tokens: int
    estimated_cost: float | None = Field(
        description="Estimate from the configured pricing registry; null if unpriced"
    )
    cost_complete: bool = Field(
        description="False if some requests had no configured price"
    )
    entries: list[UsageEntry]
    note: str = (
        "Token counts are provider-reported. Cost is an estimate from the configured "
        "pricing registry and may differ from the provider invoice."
    )


class QualityResponse(BaseModel):
    analysis_id: UUID
    report_status: QualityStatus
    metrics: ReportQualityMetrics
    evaluated_at: datetime
    note: str = (
        "Quality metrics are structural/lexical heuristics, not a measure of "
        "factual accuracy."
    )


JsonObject = dict[str, Any]
