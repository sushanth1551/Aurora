"""API contracts for technical SEO audits (Phase 2)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditCreateRequest(BaseModel):
    """Plan an audit. Nothing is fetched until the plan is approved and run."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=4, max_length=2048, description="Start URL (usually the homepage)"
    )
    max_pages: int | None = Field(
        default=None, ge=1, description="Default and cap: SEO_AUDIT_MAX_PAGES"
    )
    max_link_checks: int | None = Field(
        default=None, ge=0, description="Cap: SEO_AUDIT_MAX_LINK_CHECKS"
    )
    max_sitemaps: int | None = Field(
        default=None, ge=0, description="Cap: SEO_AUDIT_MAX_SITEMAPS"
    )


class AuditRunRequest(BaseModel):
    """Explicit approval of the plan's spending ceiling."""

    model_config = ConfigDict(extra="forbid")

    approve: Literal[True] = Field(
        description="Must be true: confirms the estimate was reviewed"
    )
    max_firecrawl_scrapes: int = Field(
        ge=1, description="At most the plan's firecrawl_scrapes_max"
    )


class AuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_url: str
    host: str
    status: str
    limits: dict[str, Any]
    estimate: dict[str, Any]
    approval: dict[str, Any] | None
    usage: dict[str, Any]
    coverage: dict[str, Any]
    site_files: dict[str, Any]
    summary: dict[str, Any]
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AuditList(BaseModel):
    items: list[AuditView]
    total: int


class PageSummary(BaseModel):
    id: UUID
    url: str
    final_url: str | None
    status_code: int | None
    status_source: str
    fetch_status: str
    fetch_source: str
    depth: int
    title: str | None
    indexability: str
    indexability_reasons: list[str]
    googlebot_allowed: bool | None


class PageList(BaseModel):
    items: list[PageSummary]
    total: int


class PageDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    final_url: str | None
    status_code: int | None
    status_source: str
    depth: int
    discovered_via: list[Any]
    redirect_chain: list[Any]
    headers: dict[str, Any]
    fetch_source: str
    fetch_status: str
    fetch_detail: str | None
    fetched_at: datetime | None
    html_sha256: str | None
    html_truncated: bool
    firecrawl_status: int | None
    googlebot_allowed: bool | None
    signals: dict[str, Any] | None
    structured_data: dict[str, Any] | None
    indexability: dict[str, Any]


class FindingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    check_id: str
    category: str
    severity: str
    title: str
    detail: str
    affected_urls: list[str]
    affected_count: int
    evidence: list[dict[str, Any]]
    evidence_state: str
    skill_refs: list[str]


class FindingList(BaseModel):
    items: list[FindingView]
    total: int


class RecommendationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_id: UUID
    check_id: str
    action: str
    rationale: str
    impact: str
    confidence: str
    effort: str
    risk: str
    tier: str
    rank: int
    affected_count: int
    verification: str
    basis: str


class RecommendationList(BaseModel):
    items: list[RecommendationView]
    total: int
