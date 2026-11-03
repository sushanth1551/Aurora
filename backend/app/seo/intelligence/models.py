"""Domain models for the SEO Intelligence layer (no persistence or HTTP here).

The layer stores what an SEO execution observed, decided and changed for one
website, so the next execution can compare against it. Every finding keeps its
evidence and an ``evidence_state``; nothing here computes rankings, traffic
forecasts or numeric SEO scores.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SITE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FINDING_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/#=?&%+-]{0,239}$")

Priority = Literal["critical", "high", "medium", "low"]
EvidenceState = Literal["observed", "reported", "inferred", "not_available"]
FindingStatus = Literal[
    "open", "in_progress", "implemented", "verified", "resolved", "rejected", "deferred"
]
ExecutionMode = Literal[
    "audit",
    "competitor-analysis",
    "content-optimization",
    "performance-review",
    "update-skills",
    "baseline-import",
    "technical-audit-import",
]
ExecutionStatus = Literal["running", "completed", "partial", "blocked", "failed"]
CallStatus = Literal["success", "partial", "unavailable", "failed", "skipped"]

ACTIVE_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "implemented"})
PRIORITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def utcnow() -> datetime:
    return datetime.now(UTC)


def validate_site_id(value: str) -> str:
    """Site ids become directory names, so they are restricted to a safe slug."""
    if not SITE_ID_RE.match(value) or len(value) > 64:
        raise ValueError("site id must be a lowercase slug of at most 64 characters")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Website configuration -------------------------------------------------------


class RepositoryLink(_Strict):
    """Where the website's source lives. AURORA only ever reads it."""

    path: str | None = None
    production_branch: str = "main"
    access: Literal["read_only"] = "read_only"
    seo_paths: dict[str, str] = {}


class Competitor(_Strict):
    domain: str
    relevance: Literal["direct", "content", "serp_only", "not_a_competitor"] = "direct"
    note: str = ""


class SiteRules(_Strict):
    """Editorial and on-page rules specific to one website (override generic ones)."""

    title_max_chars: int = Field(default=60, ge=20, le=120)
    meta_description_max_chars: int = Field(default=160, ge=50, le=320)
    min_words_blog_post: int | None = Field(default=None, ge=100)
    editorial: list[str] = []


class Integrations(_Strict):
    """Names of data sources the site can use; never credentials."""

    search_console_property: str | None = None
    analytics: list[str] = []
    mcp_servers: list[str] = []
    notes: list[str] = []


class WebsiteConfig(_Strict):
    id: str
    name: str
    domain: str
    base_url: str
    niche: str = ""
    audiences: list[str] = []
    markets: list[str] = []
    language: str = "en"
    products: list[dict[str, str]] = []
    conversion_goals: list[str] = []
    key_pages: list[str] = []
    excluded_paths: list[str] = []
    brand_name: str = ""
    competitors: list[Competitor] = []
    seed_keywords: list[str] = []
    rules: SiteRules = SiteRules()
    integrations: Integrations = Integrations()
    repository: RepositoryLink = RepositoryLink()
    report_sources: list[str] = []
    approval_required_for: list[str] = [
        "production code changes",
        "content publication",
        "sitemap or indexing submissions",
        "skill methodology changes",
    ]

    @field_validator("id")
    @classmethod
    def _site_id(cls, value: str) -> str:
        return validate_site_id(value)

    @field_validator("domain")
    @classmethod
    def _bare_domain(cls, value: str) -> str:
        value = value.strip().lower()
        if "/" in value or ":" in value or not value:
            raise ValueError("domain must be a bare host name such as example.com")
        return value


# --- Findings ----------------------------------------------------------------------


class Evidence(_Strict):
    """One observation that supports a finding."""

    source: str
    observation: str
    url: str | None = None
    observed_at: datetime | None = None
    execution_id: str | None = None


class StatusEvent(_Strict):
    at: datetime = Field(default_factory=utcnow)
    status: FindingStatus
    note: str = ""
    execution_id: str | None = None
    reference: str | None = None


class Finding(_Strict):
    """One SEO issue or opportunity, tracked across executions by a stable ``key``."""

    key: str
    issue: str
    category: str
    priority: Priority
    evidence: list[Evidence] = Field(min_length=1)
    evidence_state: EvidenceState = "observed"
    affected_urls: list[str] = []
    seo_impact: str
    recommended_action: str
    expected_outcome: str
    verification_method: str
    status: FindingStatus = "open"
    skill: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    seen_in: list[str] = []
    history: list[StatusEvent] = []

    @field_validator("key")
    @classmethod
    def _key_format(cls, value: str) -> str:
        value = value.strip().lower()
        if not FINDING_KEY_RE.match(value):
            raise ValueError("finding key must be a lowercase stable identifier")
        return value

    @field_validator("expected_outcome")
    @classmethod
    def _no_guarantees(cls, value: str) -> str:
        lowered = value.lower()
        if "guarantee" in lowered and "no guarantee" not in lowered:
            raise ValueError("expected outcomes must not promise guaranteed results")
        return value


class CheckedScope(_Strict):
    """What an execution actually re-examined, so absent findings are judged fairly.

    A previous finding counts as resolved only when its key or category was
    checked again and it was not observed; otherwise it stays "not rechecked".
    """

    keys: list[str] = []
    categories: list[str] = []


# --- Executions --------------------------------------------------------------------


class SkillUse(_Strict):
    name: str
    reason: str
    analysed: str = ""
    actions: list[str] = []
    output: str = ""
    content_hash: str | None = None


class ToolUse(_Strict):
    name: str
    kind: Literal["mcp", "api", "cli", "aurora", "file", "git", "manual", "other"]
    status: CallStatus
    calls: int = Field(default=0, ge=0)
    note: str = ""


class ChangeRecord(_Strict):
    """A change made to the website or to AURORA files during an execution."""

    description: str
    kind: Literal["file_created", "file_modified", "seo_change", "knowledge", "skill"]
    path: str | None = None
    finding_keys: list[str] = []
    reference: str | None = None
    approved_by: str | None = None


class LearningSummary(_Strict):
    learned: list[str] = []
    reused: list[str] = []
    patterns: list[str] = []
    skill_proposals: list[str] = []
    needs_approval: list[str] = []


class Comparison(_Strict):
    new: list[str] = []
    recurring: list[str] = []
    resolved: list[str] = []
    not_rechecked: list[str] = []
    reopened: list[str] = []


class Execution(_Strict):
    id: str
    site_id: str
    mode: ExecutionMode
    status: ExecutionStatus = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    summary: str = ""
    pages_analysed: list[str] = []
    reports_processed: list[str] = []
    skills_used: list[SkillUse] = []
    tools_used: list[ToolUse] = []
    findings: list[Finding] = []
    checked: CheckedScope = CheckedScope()
    changes: list[ChangeRecord] = []
    blockers: list[str] = []
    learning: LearningSummary = LearningSummary()
    comparison: Comparison | None = None
    notes: list[str] = []


class ExecutionPayload(_Strict):
    """What a skill run hands to ``record``; merged into the running execution."""

    summary: str | None = None
    pages_analysed: list[str] = []
    reports_processed: list[str] = []
    skills_used: list[SkillUse] = []
    tools_used: list[ToolUse] = []
    findings: list[Finding] = []
    checked: CheckedScope = CheckedScope()
    changes: list[ChangeRecord] = []
    blockers: list[str] = []
    learning: LearningSummary = LearningSummary()
    notes: list[str] = []


class ExecutionIndexEntry(_Strict):
    id: str
    mode: ExecutionMode
    status: ExecutionStatus
    started_at: datetime
    finished_at: datetime | None = None
    summary: str = ""
    new: int = 0
    recurring: int = 0
    resolved: int = 0


# --- Other knowledge records -------------------------------------------------------


class RejectedRecommendation(_Strict):
    key: str
    recommendation: str
    reason: str
    decided_by: str
    decided_at: datetime = Field(default_factory=utcnow)


class MetricObservation(_Strict):
    """A performance metric copied from a named source; never estimated."""

    metric: str
    value: float
    unit: str = ""
    source: str
    period: str
    scope: str = "site"
    recorded_at: datetime = Field(default_factory=utcnow)
    execution_id: str | None = None


class ResearchRecord(_Strict):
    """Keyword, competitor or content-strategy research kept as dated snapshots."""

    id: str
    kind: Literal["keyword", "competitor", "content-strategy"]
    created_at: datetime = Field(default_factory=utcnow)
    sources: list[str] = Field(min_length=1)
    summary: str
    items: list[dict[str, object]] = []
    limitations: list[str] = []
    execution_id: str | None = None
