"""Typed graph models shared by extraction, resolution, persistence and queries."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.graph.graph_schema import Assertion, Label, Rel
from app.schemas.research import utc_now

#: Rank used to keep the strongest verification status on a relationship.
STATUS_RANK = {
    "supported": 5,
    "partially_supported": 4,
    "conflicting": 3,
    "unverified": 2,
    "inferred": 1,
    "unsupported": 0,
}
VERIFIED_STATUSES = frozenset({"supported", "partially_supported"})


def stronger(a: str | None, b: str | None) -> str:
    """The stronger of two verification statuses (missing counts as weakest)."""
    return max(
        (a or "unsupported", b or "unsupported"), key=lambda s: STATUS_RANK.get(s, 0)
    )


class GraphEntity(BaseModel):
    """An entity ready to become a node (after resolution)."""

    id: str
    label: Label
    canonical_name: str
    original_names: list[str] = Field(default_factory=list)
    normalized_name: str  # NFKC, casefolded, whitespace-collapsed; punctuation kept
    match_key: str  # Module 3 normalized form used for identity
    aliases: list[str] = Field(default_factory=list)
    scope_domain: str | None = None
    source_record_ids: list[str] = Field(
        default_factory=list
    )  # research_entities.entity_key
    source_urls: list[str] = Field(default_factory=list)
    research_job_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None
    verification_status: str = "unverified"
    properties: dict[str, Any] = Field(default_factory=dict)
    derived_from_claim: bool = False


class SourceNode(BaseModel):
    id: str
    url: str
    normalized_url: str
    domain: str
    title: str | None = None
    source_type: str = "unknown"  # official | third_party | unknown
    credibility_metadata: str | None = None  # JSON text (Neo4j has no map properties)
    content_hash: str | None = None
    retrieved_at: datetime | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    research_job_ids: list[str] = Field(default_factory=list)


class ClaimNode(BaseModel):
    id: str  # research_claims.id
    evidence_id: str  # "c-<10 hex>" as used by Module 4 reports
    research_job_id: str
    subject: str
    predicate: str
    object_value: str
    claim_text: str
    evidence_excerpt: str
    source_url: str
    source_node_id: str | None = None
    source_record_id: str | None = None
    verification_status: str
    confidence: float | None = None
    extraction_method: str
    observed_at: datetime
    notes: list[str] = Field(default_factory=list)


class RelationshipRow(BaseModel):
    """One relationship assertion; several rows may target the same edge."""

    type: Rel
    source_id: str
    source_label: Label
    target_id: str
    target_label: Label
    assertion: Assertion = Assertion.DIRECT
    verification_status: str = "unverified"
    confidence: float | None = None
    extraction_method: str = "deterministic"
    observed_at: datetime | None = None
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)  # Source node ids
    source_record_ids: list[str] = Field(default_factory=list)  # research_sources.id
    basis: str | None = None
    role: str | None = None  # MENTIONS: subject | object
    excerpt_verified: bool | None = None  # SUPPORTS only
    research_job_id: str | None = None


class RejectedRelationship(BaseModel):
    type: str
    source: str
    target: str
    reason: str
    claim_id: str | None = None


class ResolutionDecision(StrEnum):
    MERGED = "merged"  # mapped onto an existing graph node
    NEW = "new"  # became its own node
    REVIEW = "review"  # kept separate; a possible duplicate needs a human


class ResolutionRecord(BaseModel):
    """Auditable outcome of resolving one PostgreSQL entity onto a graph node."""

    research_job_id: str
    entity_key: str
    label: Label
    graph_node_id: str
    stage: str  # stable_id | normalized | domain | alias | fuzzy | llm | new
    decision: ResolutionDecision
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    candidate_node_id: str | None = None
    canonical_name: str = ""
    match_key: str = ""
    scope_domain: str | None = None
    aliases: list[str] = Field(default_factory=list)


class GraphProjection(BaseModel):
    """Everything one sync writes, built before any write happens."""

    research_job_id: str
    entities: list[GraphEntity] = Field(default_factory=list)
    sources: list[SourceNode] = Field(default_factory=list)
    claims: list[ClaimNode] = Field(default_factory=list)
    relationships: list[RelationshipRow] = Field(default_factory=list)
    provenance: list[RelationshipRow] = Field(
        default_factory=list
    )  # SUPPORTS + MENTIONS
    rejected: list[RejectedRelationship] = Field(default_factory=list)
    resolutions: list[ResolutionRecord] = Field(default_factory=list)


class SyncCounts(BaseModel):
    entities_processed: int = 0
    entities_created: int = 0
    entities_updated: int = 0
    duplicates_detected: int = 0
    review_candidates: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    relationships_rejected: int = 0
    claims_linked: int = 0
    source_nodes_created: int = 0
    source_nodes_updated: int = 0
    claim_nodes_created: int = 0
    claim_nodes_updated: int = 0
    retries: int = 0
    failed_records: int = 0


class GraphNode(BaseModel):
    id: str
    label: Label
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    type: Rel
    source_id: str
    target_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class SyncRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    research_job_id: UUID | None = None
    mode: str = "job"
    dry_run: bool = False
    status: str = "running"
    counts: SyncCounts = Field(default_factory=SyncCounts)
    rejected: list[RejectedRelationship] = Field(default_factory=list)
    review: list[ResolutionRecord] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_ms: int = 0
