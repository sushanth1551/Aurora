"""API contracts for Search Console monitoring (Phase 6)."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.monitoring.models import Thresholds


class DisconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revoke: bool = Field(
        default=False,
        description="Also ask Google to revoke the token (one Google call)",
    )


class PropertySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_url: str = Field(min_length=4, max_length=512)


class SiteView(BaseModel):
    site_url: str
    permission_level: str
    verified: bool


class SyncPlanRequest(BaseModel):
    """Plan a fetch of Search Console data. No Google request is made until it is approved."""

    model_config = ConfigDict(extra="forbid")

    property: str | None = Field(
        default=None, max_length=512, description="Default: the selected property"
    )
    start_date: date | None = None
    end_date: date | None = None
    max_requests: int | None = Field(
        default=None, ge=2, description="Cap: GSC_MAX_REQUESTS_PER_SYNC"
    )


class SyncApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: Literal[True]
    max_requests: int = Field(ge=2, description="At most the plan's request ceiling")


class SyncView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property: str
    start_date: date
    end_date: date
    status: str
    source: str
    limits: dict[str, Any]
    estimate: dict[str, Any]
    approval: dict[str, Any] | None
    usage: dict[str, Any]
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property: str | None = Field(default=None, max_length=512)
    page_url: str = Field(min_length=8, max_length=2048)
    keyword: str | None = Field(default=None, max_length=300)
    article_id: UUID | None = None
    article_version_id: UUID | None = None
    publication_id: UUID | None = None
    published_at: datetime | None = None


class MappingActive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class MappingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property: str
    page_url: str
    keyword: str | None
    article_id: UUID | None
    article_version_id: UUID | None
    publication_id: UUID | None
    source: str
    published_at: datetime | None
    active: bool
    created_at: datetime


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property: str | None = Field(default=None, max_length=512)
    thresholds: Thresholds = Field(default_factory=Thresholds)


class AnalysisView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property: str
    status: str
    params: dict[str, Any]
    window: dict[str, Any]
    summary: dict[str, Any]
    notes: list[str]
    source: str
    created_at: datetime


class InsightView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    analysis_id: UUID
    kind: str
    subject_type: str
    subject: str
    evidence: dict[str, Any]
    impact: str
    confidence: str
    effort: str
    risk: str
    tier: str
    rank: int
    action: str
    rationale: str
    verification: str
    basis: str
    refresh_candidate: bool
    article_id: UUID | None
    status: str


class InsightStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "accepted", "dismissed"]


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    insight_id: UUID | None
    property: str
    page_url: str
    article_id: UUID | None
    article_version_id: UUID | None
    brief: dict[str, Any]
    status: str
    requires_new_approval: bool
    note: str | None
    created_at: datetime
    updated_at: datetime


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "draft_requested", "draft_ready", "dismissed"]
    note: str | None = Field(default=None, max_length=2000)


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    property: str | None = Field(default=None, max_length=512)
    kind: Literal["sync", "analyze"]
    interval_hours: int = Field(ge=6, le=24 * 30)
    lookback_days: int = Field(default=7, ge=1, le=90)
    budget_requests: int = Field(
        default=2, ge=0, description="Request ceiling per run (sync only)"
    )


class ScheduleApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: Literal[True]
    approved_by: str = Field(default="user", min_length=1, max_length=120)
    budget_requests: int = Field(
        ge=0, description="Must equal the schedule's budget_requests"
    )


class ScheduleEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    property: str
    kind: str
    interval_hours: int
    lookback_days: int
    budget_requests: int
    approved_by: str | None
    approved_at: datetime | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
