"""Domain models for keyword research (no persistence or HTTP here)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Level = Literal["high", "medium", "low"]
Source = Literal["seed", "site_topic", "serp_paa", "serp_related", "template"]
Intent = Literal["informational", "commercial", "transactional", "navigational"]


class MetricValue(BaseModel):
    """A provider metric. ``value`` stays ``None`` unless a provider measured it."""

    value: float | None = None
    status: Literal["measured", "not_available"] = "not_available"
    source: str | None = None
    observed_at: datetime | None = None


def unavailable_metrics() -> dict[str, MetricValue]:
    """SerpApi returns no volume, difficulty or CPC, so these are never filled in."""
    return {name: MetricValue() for name in ("search_volume", "difficulty", "cpc")}


class Candidate(BaseModel):
    keyword: str
    normalized: str
    source: Source
    source_detail: str | None = Field(
        default=None, description="Parent keyword or page"
    )
    intent: Intent | None = None
    intent_basis: str | None = None
    cluster: str | None = None
    searched: bool = False
    metrics: dict[str, MetricValue] = Field(default_factory=unavailable_metrics)


class SerpEvidence(BaseModel):
    """Compact, citable summary of one stored SERP snapshot."""

    snapshot_id: str | None = None
    query: str
    observed_at: datetime | None = None
    organic_count: int = 0
    top_domains: list[str] = []
    site_position: int | None = None
    paa_count: int = 0
    related_count: int = 0
    featured_snippet_domain: str | None = None
    ai_overview: str = "absent"
    ai_overview_cites_site: bool | None = None
    shopping: bool = False
    ads: int = 0
    cache_hit: bool = False


class Competitor(BaseModel):
    domain: str
    appearances: int
    keywords: list[str]
    best_position: int
    positions: dict[str, int]
    classification: str = (
        "serp_competitor"  # observed in results; not necessarily a business rival
    )


class GapEvidence(BaseModel):
    domain: str
    url: str
    query: str
    position: int
    excerpt: str


class GapTerm(BaseModel):
    term: str
    kind: Literal["phrase", "question"]
    competitor_domains: list[str]
    queries: list[str]
    evidence: list[GapEvidence]
    basis: Literal["observed", "inferred"] = "observed"


class Opportunity(BaseModel):
    keyword: str
    normalized: str
    intent: Intent | None
    intent_basis: str | None
    cluster: str | None
    action: str  # write_new | improve_existing | monitor | low_evidence | deprioritized
    impact: Level
    confidence: Level
    effort: Level
    risk: Level
    tier: str
    rank: int = 0
    rationale: list[str]
    serp: SerpEvidence
    gap_terms: list[str]
    questions: list[str]
    metrics: dict[str, MetricValue] = Field(default_factory=unavailable_metrics)
    basis: Literal["observed", "inferred"] = "inferred"


class ResearchLimits(BaseModel):
    max_queries: int
    max_candidates: int
    max_seeds: int
    include_expansions: bool = False
    cache_ttl_hours: int
    min_interval_seconds: float


class ResearchEstimate(BaseModel):
    serpapi_searches_max: int
    cached_snapshots_possible: int = 0
    estimated_cost: float | None = None
    cost_note: str
    notes: list[str] = []


class ResearchUsage(BaseModel):
    serpapi_searches: int = 0
    cache_hits: int = 0
    failures: int = 0
    estimated_cost: float | None = None
    stopped_reason: str | None = None


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
