"""Domain models for the intelligence engine (Module 4).

Evidence IDs are strings: ``c-<10 hex>`` for a Module 3 claim and ``s-<10 hex>``
for a stored source excerpt, derived from the record UUID so they are stable
across context rebuilds. Any ID that is not in the current context is invalid.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intelligence import EntityType, SourceKind, VerificationStatus
from app.schemas.research import utc_now


def new_id() -> str:
    """Short opaque ID for generated items."""
    return uuid4().hex[:12]


class AnalysisType(StrEnum):
    """Report/analysis kinds."""

    EXECUTIVE_SUMMARY = "executive_summary"
    DETAILED_REPORT = "detailed_report"
    COMPANY_INTELLIGENCE = "company_intelligence"
    PRODUCT_COMPARISON = "product_comparison"
    TECHNOLOGY_ANALYSIS = "technology_analysis"
    COMPETITOR_INTELLIGENCE = "competitor_intelligence"
    MARKET_RESEARCH = "market_research"


class AnalysisStatus(StrEnum):
    """Lifecycle of an analysis."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    REQUIRES_REVIEW = "requires_review"


class QualityStatus(StrEnum):
    """Outcome of the quality gates. ``passed`` is not a statement of accuracy."""

    PASSED = "passed"
    REQUIRES_REVIEW = "requires_review"
    FAILED = "failed"


class FindingKind(StrEnum):
    """What a statement is. Only FACT and OBSERVATION need evidence to stand."""

    FACT = "fact"  # directly stated by a cited source
    OBSERVATION = "observation"  # analytical reading of cited evidence
    HYPOTHESIS = "hypothesis"  # analyst-generated; may lack evidence
    RECOMMENDATION = "recommendation"


class Level(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ItemStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    UNANSWERED = "unanswered"
    ADDRESSED = "addressed"
    UNRESOLVED = "unresolved"
    DISCLOSED = "disclosed"
    PROPOSED = "proposed"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class Tier(StrEnum):
    """Source credibility tier (about the source, not about any claim)."""

    OFFICIAL_DOCS = "official_documentation"
    OFFICIAL_PRICING = "official_pricing"
    OFFICIAL_SITE = "official_website"
    GOVERNMENT = "government_regulatory"
    PUBLICATION = "industry_publication"
    THIRD_PARTY = "third_party"
    DIRECTORY = "directory"
    SNIPPET = "search_snippet"
    UNKNOWN = "unknown"


# -- evidence and context --------------------------------------------------------


class SourceCredibility(BaseModel):
    """Explainable credibility: a score plus the factors that produced it."""

    score: float = Field(ge=0, le=1)
    tier: Tier = Tier.UNKNOWN
    factors: list[str] = Field(default_factory=list)


class EvidenceKind(StrEnum):
    CLAIM = "claim"
    SOURCE_EXCERPT = "source_excerpt"


class EvidenceItem(BaseModel):
    """One citable piece of evidence with full provenance."""

    evidence_id: str
    kind: EvidenceKind
    research_job_id: UUID
    claim_id: UUID | None = None
    source_id: UUID | None = None
    entity: str | None = None
    entity_type: EntityType | None = None
    predicate: str | None = None
    value: str | None = None
    text: str
    excerpt: str
    source_url: str
    source_title: str | None = None
    source_domain: str
    source_content_hash: str | None = None
    source_kind: SourceKind = SourceKind.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    extraction_confidence: float | None = Field(default=None, ge=0, le=1)
    observed_at: datetime | None = None
    publication_date: str | None = None
    credibility: SourceCredibility = Field(
        default_factory=lambda: SourceCredibility(score=0.2)
    )
    relevance: float = 0.0
    rank_score: float = 0.0
    token_estimate: int = 0


class SourceInfo(BaseModel):
    """A deduplicated source (stable ID = research_sources.id when known)."""

    source_id: UUID | None = None
    research_job_id: UUID
    url: str
    title: str | None = None
    domain: str
    kind: SourceKind = SourceKind.UNKNOWN
    content_hash: str | None = None
    extracted_at: datetime | None = None
    has_full_text: bool = False
    credibility: SourceCredibility = Field(
        default_factory=lambda: SourceCredibility(score=0.2)
    )
    evidence_ids: list[str] = Field(default_factory=list)


class InformationGap(BaseModel):
    """Something the objective needs that the evidence does not contain."""

    gap_id: str = Field(default_factory=new_id)
    description: str
    related_entity: str | None = None
    predicate: str | None = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    priority: int = Field(default=3, ge=1, le=5)
    critical: bool = False
    origin: str = "deterministic"
    status: ItemStatus = ItemStatus.OPEN


class Contradiction(BaseModel):
    """Conflicting evidence, preserved rather than resolved."""

    contradiction_id: str = Field(default_factory=new_id)
    subject: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[UUID] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    origin: str = "deterministic"
    resolution_status: ItemStatus = ItemStatus.UNRESOLVED


class UnifiedResearchContext(BaseModel):
    """Everything the engine may reason over, built from stored research."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    context_id: UUID = Field(default_factory=uuid4)
    research_job_id: UUID
    job_ids: list[UUID] = Field(default_factory=list)
    objective: str
    version: int = 1
    companies: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    sources: list[SourceInfo] = Field(default_factory=list)
    verified_ids: list[str] = Field(default_factory=list)
    unverified_ids: list[str] = Field(default_factory=list)
    conflicts: list[Contradiction] = Field(default_factory=list)
    missing_information: list[InformationGap] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)
    context_hash: str = ""
    token_estimate: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    #: Full source texts for excerpt verification. Runtime only: never serialized.
    source_texts: dict[str, str] = Field(default_factory=dict, exclude=True)

    def evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {e.evidence_id: e for e in self.evidence}


# -- reasoning -----------------------------------------------------------------------


class ResearchQuestion(BaseModel):
    question_id: str = Field(default_factory=new_id)
    question: str
    related_entity: str | None = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    priority: int = Field(default=3, ge=1, le=5)
    status: ItemStatus = ItemStatus.OPEN
    created_at: datetime = Field(default_factory=utc_now)


class Confidence(BaseModel):
    """Separate confidence dimensions. ``overall`` is the weakest of the four."""

    extraction: float | None = None
    source_credibility: float | None = None
    evidence_support: float | None = None
    analytical: Level = Level.LOW
    overall: Level = Level.LOW
    rubric: str = ""


class ResearchFinding(BaseModel):
    finding_id: str = Field(default_factory=new_id)
    description: str
    kind: FindingKind = FindingKind.OBSERVATION
    related_entity: str | None = None
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    status: ItemStatus = ItemStatus.OPEN
    created_at: datetime = Field(default_factory=utc_now)


class FollowUpResearchQuery(BaseModel):
    query_id: str = Field(default_factory=new_id)
    query: str
    target_name: str | None = None
    reason: str = ""
    gap_id: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    status: ItemStatus = ItemStatus.PROPOSED
    rejection_reason: str | None = None
    result_job_id: UUID | None = None


class ResearchDecision(BaseModel):
    """A short, evidence-referenced decision record. Never a reasoning trace."""

    iteration: int
    decision: str  # "continue" | "stop"
    stop_reason: str | None = None
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ReasoningResult(BaseModel):
    questions: list[ResearchQuestion] = Field(default_factory=list)
    findings: list[ResearchFinding] = Field(default_factory=list)
    gaps: list[InformationGap] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    followups: list[FollowUpResearchQuery] = Field(default_factory=list)
    decisions: list[ResearchDecision] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    iterations: int = 0
    stop_reason: str = ""
    llm_degraded: bool = False


# -- synthesis -----------------------------------------------------------------------


class Statement(BaseModel):
    """A sentence with its evidence. FACT/OBSERVATION must cite evidence."""

    statement: str
    kind: FindingKind = FindingKind.FACT
    evidence_ids: list[str] = Field(default_factory=list)
    entity: str | None = None
    confidence: Confidence | None = None
    limitations: list[str] = Field(default_factory=list)


class ComparisonDimension(BaseModel):
    name: str
    by_entity: dict[str, list[Statement]] = Field(default_factory=dict)
    note: str | None = None


class _ComparisonBase(BaseModel):
    entities: list[str] = Field(default_factory=list)
    dimensions: list[ComparisonDimension] = Field(default_factory=list)
    common_features: list[Statement] = Field(default_factory=list)
    differentiators: list[Statement] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class CompanyComparison(_ComparisonBase):
    """Companies compared on documented products, services, pricing, customers."""


class ProductComparison(_ComparisonBase):
    """Products compared on documented features, integrations and pricing."""


class TechnologyComparison(_ComparisonBase):
    """Technologies *mentioned* (a mention is not proof of use)."""


class CompetitorAnalysis(BaseModel):
    subject: str | None = None
    competitors: list[str] = Field(default_factory=list)
    positioning: list[Statement] = Field(default_factory=list)
    documented_strengths: list[Statement] = Field(default_factory=list)
    comparative_advantages: list[Statement] = Field(default_factory=list)
    observed_limitations: list[Statement] = Field(default_factory=list)
    hypotheses: list[Statement] = Field(default_factory=list)
    missing_public_information: list[str] = Field(default_factory=list)


class MarketTrend(BaseModel):
    trend_id: str = Field(default_factory=new_id)
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    observation_count: int = 0
    source_count: int = 0
    related_entities: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    limitations: list[str] = Field(default_factory=list)


class _Strategic(BaseModel):
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: Confidence = Field(default_factory=Confidence)
    limitations: list[str] = Field(default_factory=list)
    is_hypothesis: bool = False


class Opportunity(_Strategic):
    opportunity_id: str = Field(default_factory=new_id)


class Threat(_Strategic):
    threat_id: str = Field(default_factory=new_id)


class IntelligenceFinding(Statement):
    finding_id: str = Field(default_factory=new_id)
    finding_type: str = "observation"
    related_entities: list[str] = Field(default_factory=list)


class IntelligenceConclusion(Statement):
    conclusion_id: str = Field(default_factory=new_id)
    finding_ids: list[str] = Field(default_factory=list)


class SynthesisResult(BaseModel):
    company_comparison: CompanyComparison | None = None
    product_comparison: ProductComparison | None = None
    technology_comparison: TechnologyComparison | None = None
    competitor_analysis: CompetitorAnalysis | None = None
    trends: list[MarketTrend] = Field(default_factory=list)
    opportunities: list[Opportunity] = Field(default_factory=list)
    threats: list[Threat] = Field(default_factory=list)
    findings: list[IntelligenceFinding] = Field(default_factory=list)
    conclusions: list[IntelligenceConclusion] = Field(default_factory=list)
    executive_summary: list[Statement] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    llm_degraded: bool = False


# -- report and quality ----------------------------------------------------------------


class ReportSourceRef(BaseModel):
    """A citation target. URLs come only from stored sources."""

    evidence_id: str
    source_url: str
    source_title: str | None = None
    source_domain: str
    source_kind: SourceKind = SourceKind.UNKNOWN
    excerpt: str = ""
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    observed_at: datetime | None = None
    credibility: float | None = None


class CitationIssue(BaseModel):
    location: str
    statement: str
    evidence_id: str | None = None
    problem: str  # missing_reference | wrong_job | missing_source | excerpt_not_found | weak_support | unsupported_figure | no_citation
    severity: str = "error"  # error | warning


class ReportQualityMetrics(BaseModel):
    """Heuristic report-quality metrics; none of these measures factual accuracy."""

    citation_coverage: float = 0.0
    evidence_coverage: float = 0.0
    unsupported_claim_count: int = 0
    invalid_citation_count: int = 0
    contradiction_count: int = 0
    undisclosed_contradiction_count: int = 0
    relevance_score: float = 0.0
    completeness_score: float = 0.0
    source_diversity: float = 0.0
    source_freshness: float | None = None
    structural_validity: bool = False
    missing_sections: list[str] = Field(default_factory=list)
    factual_statements: int = 0
    overall_status: QualityStatus = QualityStatus.REQUIRES_REVIEW
    issues: list[CitationIssue] = Field(default_factory=list)
    gate_failures: list[str] = Field(default_factory=list)
    llm_judgement: dict[str, Any] | None = None


class IntelligenceReport(BaseModel):
    """The reusable report schema; rendered deterministically to JSON/Markdown."""

    report_id: UUID = Field(default_factory=uuid4)
    research_job_id: UUID
    analysis_id: UUID
    report_type: AnalysisType
    version: int = 1
    title: str
    objective: str
    executive_summary: list[Statement] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    key_findings: list[IntelligenceFinding] = Field(default_factory=list)
    company_analysis: list[Statement] = Field(default_factory=list)
    product_analysis: list[Statement] = Field(default_factory=list)
    technology_analysis: list[Statement] = Field(default_factory=list)
    comparisons: dict[str, Any] = Field(default_factory=dict)
    competitor_analysis: CompetitorAnalysis | None = None
    trends: list[MarketTrend] = Field(default_factory=list)
    opportunities: list[Opportunity] = Field(default_factory=list)
    threats: list[Threat] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    information_gaps: list[InformationGap] = Field(default_factory=list)
    conclusions: list[IntelligenceConclusion] = Field(default_factory=list)
    sources: list[ReportSourceRef] = Field(default_factory=list)
    research_date: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality: ReportQualityMetrics | None = None
    limitations: list[str] = Field(default_factory=list)
