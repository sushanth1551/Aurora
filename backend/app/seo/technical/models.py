"""Domain models for technical SEO audits (no persistence or HTTP here)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.seo.technical.html_signals import PageSignals
from app.seo.technical.structured_data import StructuredDataReport

Severity = Literal["high", "medium", "low", "info"]
Level = Literal["high", "medium", "low"]
EvidenceState = Literal["observed", "inferred", "not_available"]

INDEXABILITY_NOTE = (
    "Based on signals observed by AURORA only. A 200 response is not proof that a page "
    "is indexed; actual index status needs Search Console URL Inspection."
)


class AuditLimits(BaseModel):
    max_pages: int
    max_link_checks: int
    max_sitemaps: int
    max_duration_seconds: int


class AuditEstimate(BaseModel):
    """Upper bounds shown before approval (nothing is spent before approval)."""

    firecrawl_scrapes_max: int
    direct_requests_max: int
    notes: list[str] = []


class AuditUsage(BaseModel):
    firecrawl_scrapes: int = 0
    firecrawl_failures: int = 0
    direct_requests: int = 0


class HopView(BaseModel):
    url: str
    status: int
    method: str


class Indexability(BaseModel):
    state: Literal["indexable_candidate", "not_indexable", "unknown"]
    reasons: list[str] = []
    note: str = INDEXABILITY_NOTE


class PageResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    url: str
    depth: int = 0
    discovered_via: list[str] = []
    final_url: str | None = None
    status_code: int | None = None
    status_source: Literal["http_trace", "firecrawl_reported", "none"] = "none"
    redirect_chain: list[HopView] = []
    headers: dict[str, str] = {}
    fetch_source: Literal["firecrawl_raw_html", "http_only", "not_fetched"] = (
        "not_fetched"
    )
    fetch_status: str = "pending"
    fetch_detail: str | None = None
    fetched_at: datetime | None = None
    html_sha256: str | None = None
    html_truncated: bool = False
    firecrawl_status: int | None = None
    googlebot_allowed: bool | None = None
    signals: PageSignals | None = None
    structured_data: StructuredDataReport | None = None
    indexability: Indexability = Indexability(state="unknown")


class LinkCheck(BaseModel):
    url: str
    status: int | None = None
    final_url: str | None = None
    redirect_count: int = 0
    outcome: Literal["ok", "redirect", "broken", "unverified", "skipped_robots"] = (
        "unverified"
    )
    referrers: list[str] = []
    anchors: list[str] = []


class EvidenceItem(BaseModel):
    """What was observed, where, from which source and when."""

    url: str
    source: str  # http_trace | firecrawl_raw_html | robots_txt | sitemap | link_check
    observed: dict[str, Any] = {}
    observed_at: datetime | None = None
    page_id: UUID | None = None
    html_sha256: str | None = None


class Finding(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    check_id: str
    category: str
    severity: Severity
    title: str
    detail: str
    affected_urls: list[str] = []
    affected_count: int = 0
    evidence: list[EvidenceItem] = []
    evidence_state: EvidenceState = "observed"
    skill_refs: list[str] = []


class Recommendation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    check_id: str
    action: str
    rationale: str
    impact: Level
    confidence: Level
    effort: Level
    risk: Level
    tier: str
    rank: int = 0
    affected_count: int = 0
    verification: str
    basis: EvidenceState = "observed"


class AuditCoverage(BaseModel):
    pages_audited: int = 0
    pages_discovered: int = 0
    pages_not_fetched: int = 0
    sitemap_urls_found: int = 0
    link_targets_found: int = 0
    link_targets_checked: int = 0
    stopped_reason: str | None = None
    not_measured: list[str] = [
        "Core Web Vitals (LCP, INP, CLS): no field or lab data source is connected.",
        "Index status: requires Search Console URL Inspection (not connected).",
        "Raw-versus-rendered HTML differences: Firecrawl returns browser-rendered HTML.",
    ]
