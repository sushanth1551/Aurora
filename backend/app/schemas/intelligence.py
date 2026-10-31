"""Schemas for the website intelligence engine (Module 3).

Pipeline data (candidates, pages, documents, entities, claims, reports) and the
API request/response models. Nothing here holds credentials. Facts always travel
with their source: ``ResearchClaim`` is the unit of evidence.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.research import ExtractedPage, utc_now


class IntelligenceStatus(StrEnum):
    """Lifecycle of a website-intelligence job."""

    PENDING = "pending"
    DISCOVERING = "discovering"
    MAPPING = "mapping"
    CRAWLING = "crawling"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    REPORTING = "reporting"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"


class SubjectType(StrEnum):
    """What the research request is about."""

    COMPANY = "company"
    PRODUCT = "product"
    SERVICE = "service"
    TECHNOLOGY = "technology"
    INDUSTRY = "industry"
    COMPETITOR = "competitor"
    CATEGORY = "category"


class OfficialStatus(StrEnum):
    """Whether a website is established as the subject's official site."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    THIRD_PARTY = "third_party"
    MISMATCH = "mismatch"  # neither domain nor result text relates to the name


class WebsiteType(StrEnum):
    """Coarse nature of a discovered website."""

    OFFICIAL = "official"
    DIRECTORY = "directory"
    NEWS = "news"
    SOCIAL = "social"
    MARKETPLACE = "marketplace"
    DOCUMENTATION = "documentation"
    UNKNOWN = "unknown"


class PageType(StrEnum):
    """Classification of a page within a website."""

    HOMEPAGE = "homepage"
    ABOUT = "about"
    COMPANY = "company"
    PRODUCT = "product"
    SERVICE = "service"
    PRICING = "pricing"
    FEATURES = "features"
    DOCUMENTATION = "documentation"
    API_DOCUMENTATION = "api_documentation"
    BLOG = "blog"
    CASE_STUDY = "case_study"
    CUSTOMER_STORY = "customer_story"
    INTEGRATIONS = "integrations"
    SECURITY = "security"
    CAREERS = "careers"
    CONTACT = "contact"
    UNKNOWN = "unknown"


class EntityType(StrEnum):
    """Kinds of extracted entity."""

    COMPANY = "company"
    PRODUCT = "product"
    SERVICE = "service"
    TECHNOLOGY = "technology"
    PRICING = "pricing"
    FEATURE = "feature"
    TARGET_CUSTOMER = "target_customer"


class ExtractionMethod(StrEnum):
    """How a fact was obtained."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


class VerificationStatus(StrEnum):
    """How well a claim is backed by retrieved source content."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class SourceKind(StrEnum):
    """Whether a source is owned by the subject."""

    OFFICIAL = "official"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


class ReportType(StrEnum):
    """Supported report kinds."""

    COMPANY_PROFILE = "company_profile"
    PRODUCT_PROFILE = "product_profile"
    SERVICE_PROFILE = "service_profile"
    TECHNOLOGY_PROFILE = "technology_profile"
    COMPETITOR_PROFILE = "competitor_profile"
    INDUSTRY_REPORT = "industry_report"


# -- discovery --------------------------------------------------------------


class WebsiteCandidate(BaseModel):
    """A website considered relevant to the research subject."""

    candidate_id: UUID = Field(default_factory=uuid4)
    website_name: str
    domain: str
    homepage_url: str
    title: str | None = None
    description: str | None = None
    search_query: str
    search_position: int = Field(ge=1)
    relevance_score: float = Field(ge=0, le=1)
    website_type: WebsiteType = WebsiteType.UNKNOWN
    official_status: OfficialStatus = OfficialStatus.UNVERIFIED
    discovery_source: str = "serpapi"
    confidence: float = Field(ge=0, le=1)
    selection_reasons: list[str] = Field(default_factory=list)
    discovery_timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


# -- mapping and crawling -----------------------------------------------------


class MappedPage(BaseModel):
    """A URL found inside a website, classified and scored."""

    url: str
    normalized_url: str
    domain: str
    title: str | None = None
    page_type: PageType = PageType.UNKNOWN
    relevance_score: float = Field(default=0.0, ge=0, le=1)
    discovery_method: str = "firecrawl_map"
    mapped_at: datetime = Field(default_factory=utc_now)
    crawl_eligible: bool = True
    exclusion_reason: str | None = None
    depth: int = 0


class CrawlStatus(StrEnum):
    """Outcome of a crawl."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


class PageCrawlResult(BaseModel):
    """Per-page crawl outcome with provenance."""

    url: str
    normalized_url: str
    status: str  # succeeded | failed | skipped
    page: ExtractedPage | None = None
    error: str | None = None
    attempts: int = 0
    method: str = "batch_scrape"  # batch_scrape | scrape


class CrawlAuditEntry(BaseModel):
    """One line in the crawl audit trail."""

    at: datetime = Field(default_factory=utc_now)
    url: str
    action: str  # selected | skipped | scraped | failed | retried | robots_blocked
    detail: str | None = None


class CrawlResult(BaseModel):
    """Everything that happened while crawling one website."""

    website: str
    requested_pages: int = 0
    processed_pages: int = 0
    successful_pages: int = 0
    failed_pages: int = 0
    skipped_pages: int = 0
    page_results: list[PageCrawlResult] = Field(default_factory=list)
    crawl_status: CrawlStatus = CrawlStatus.EMPTY
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    errors: list[str] = Field(default_factory=list)
    audit: list[CrawlAuditEntry] = Field(default_factory=list)


# -- content processing -------------------------------------------------------


class ContentSection(BaseModel):
    """A heading-delimited slice of a document, attributable to its source."""

    section_id: str
    heading: str | None = None
    level: int = 0
    text: str
    order: int


class DocumentStatus(StrEnum):
    """Processing outcome for a page."""

    PROCESSED = "processed"
    EMPTY = "empty"
    DUPLICATE = "duplicate"


class ProcessedDocument(BaseModel):
    """A page after normalization; the original content is kept separately."""

    document_id: UUID = Field(default_factory=uuid4)
    source_url: str
    normalized_url: str
    canonical_url: str | None = None
    title: str | None = None
    page_type: PageType = PageType.UNKNOWN
    original_markdown: str = ""
    normalized_markdown: str = ""
    original_hash: str | None = None
    content_hash: str | None = None
    sections: list[ContentSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    published_at: str | None = None
    modified_at: str | None = None
    status: DocumentStatus = DocumentStatus.PROCESSED
    duplicate_of: str | None = None
    truncated: bool = False
    processed_at: datetime = Field(default_factory=utc_now)


# -- extracted entities -------------------------------------------------------


class _Evidenced(BaseModel):
    source_urls: list[str] = Field(default_factory=list)


class CompanyInfo(_Evidenced):
    """Company facts; every field is None unless stated in the source."""

    name: str
    official_website: str | None = None
    description: str | None = None
    industry: str | None = None
    headquarters: str | None = None
    founded_year: int | None = None
    target_markets: list[str] = Field(default_factory=list)
    target_customers: list[str] = Field(default_factory=list)
    company_size: str | None = None
    business_model: str | None = None
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class ProductInfo(_Evidenced):
    """Product facts."""

    name: str
    description: str | None = None
    category: str | None = None
    features: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    supported_platforms: list[str] = Field(default_factory=list)
    technical_capabilities: list[str] = Field(default_factory=list)
    target_users: list[str] = Field(default_factory=list)
    pricing_summary: str | None = None
    product_url: str | None = None


class ServiceInfo(_Evidenced):
    """Service facts."""

    name: str
    description: str | None = None
    category: str | None = None
    target_customers: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    service_url: str | None = None


class TechnologyInfo(_Evidenced):
    """Technology mentioned in the sources."""

    name: str
    category: str | None = None
    description: str | None = None
    use_cases: list[str] = Field(default_factory=list)
    related_products: list[str] = Field(default_factory=list)
    documentation_urls: list[str] = Field(default_factory=list)


class PricingInfo(_Evidenced):
    """A publicly stated price. Amount/currency/interval are never inferred."""

    product_or_service: str | None = None
    plan_name: str | None = None
    price_amount: Decimal | None = None
    currency: str | None = None
    billing_interval: str | None = None
    pricing_model: str | None = None
    free_tier: bool | None = None
    trial_available: bool | None = None
    source_url: str
    last_verified_at: datetime = Field(default_factory=utc_now)
    raw_text: str | None = None


class FeatureInfo(_Evidenced):
    """A feature stated for a product."""

    name: str
    description: str | None = None
    product: str | None = None
    category: str | None = None


class TargetCustomerInfo(_Evidenced):
    """A customer segment named in the sources."""

    segment: str
    industry: str | None = None
    business_size: str | None = None
    use_case: str | None = None


class ExtractedEntity(BaseModel):
    """An entity as extracted (before resolution), with its typed payload."""

    entity_type: EntityType
    name: str
    data: dict[str, Any] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)
    scope_domain: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class ResolvedEntity(BaseModel):
    """A deduplicated entity with a stable id and its match history."""

    entity_id: UUID
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)
    scope_domain: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list)
    original_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


# -- evidence ---------------------------------------------------------------


class ResearchClaim(BaseModel):
    """One fact with its provenance. Extraction confidence is not certainty."""

    claim_id: UUID = Field(default_factory=uuid4)
    subject_entity: str
    subject_type: EntityType | None = None
    scope_domain: str | None = None
    entity_id: UUID | None = None
    predicate: str
    object_value: str
    claim_text: str
    source_url: str
    source_title: str | None = None
    source_domain: str
    source_content_hash: str | None = None
    source_kind: SourceKind = SourceKind.UNKNOWN
    evidence_excerpt: str
    extraction_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC
    confidence_score: float = Field(default=0.5, ge=0, le=1)
    observed_at: datetime = Field(default_factory=utc_now)
    publication_date: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    notes: list[str] = Field(default_factory=list)


class EvidenceCoverage(BaseModel):
    """How much of the extracted information is backed by verified evidence."""

    total_claims: int = 0
    supported: int = 0
    partially_supported: int = 0
    conflicting: int = 0
    unsupported: int = 0
    unverified: int = 0
    coverage_ratio: float = 0.0
    entities_total: int = 0
    entities_with_supported_claim: int = 0
    pricing_found: bool = False


class ClaimConflict(BaseModel):
    """Claims about the same fact with different values (all are kept)."""

    subject_entity: str
    predicate: str
    values: list[str]
    claim_ids: list[UUID]


# -- reports ---------------------------------------------------------------

NOT_FOUND = "not publicly found"


class ReportSource(BaseModel):
    """A source cited by a report."""

    url: str
    title: str | None = None
    domain: str
    kind: SourceKind = SourceKind.UNKNOWN
    observed_at: datetime | None = None


class CompanyReportEntry(BaseModel):
    """Report section for one researched website/company."""

    name: str
    official_website: str | None = None
    official_status: OfficialStatus = OfficialStatus.UNVERIFIED
    overview: str = NOT_FOUND
    industry: str = NOT_FOUND
    headquarters: str = NOT_FOUND
    founded_year: str = NOT_FOUND
    company_size: str = NOT_FOUND
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    pricing: list[str] = Field(default_factory=list)
    pricing_status: str = NOT_FOUND
    target_customers: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    competitor_relationships: list[str] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    source_urls: list[str] = Field(default_factory=list)


class ResearchReportData(BaseModel):
    """Structured report; Markdown is rendered from this, never the reverse."""

    report_type: ReportType
    subject: str
    research_date: datetime = Field(default_factory=utc_now)
    companies: list[CompanyReportEntry] = Field(default_factory=list)
    comparison: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    unverified_information: list[str] = Field(default_factory=list)
    conflicting_information: list[str] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    notes: list[str] = Field(default_factory=list)


class GeneratedReport(BaseModel):
    """A report in both renderings."""

    report_id: UUID = Field(default_factory=uuid4)
    report_type: ReportType
    data: ResearchReportData
    markdown: str
    generated_at: datetime = Field(default_factory=utc_now)


# -- API --------------------------------------------------------------------


class _Request(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class DiscoverWebsitesRequest(_Request):
    """Find websites relevant to a subject."""

    query: str = Field(min_length=2, max_length=300)
    subject_type: SubjectType = SubjectType.COMPANY
    target_names: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Specific companies/products to look up by name",
    )
    max_websites: int | None = Field(default=None, ge=1, le=50)
    country: str | None = None
    language: str | None = None

    @field_validator("country", "language")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("target_names")
    @classmethod
    def _names(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [v.strip() for v in value if v.strip()]
        if any(len(v) > 120 for v in cleaned):
            raise ValueError("target names must be at most 120 characters")
        return cleaned


class WebsiteDiscoveryResponse(BaseModel):
    """Discovered websites for a stored job."""

    job_id: UUID
    query: str
    status: IntelligenceStatus
    websites: list["StoredWebsite"] = Field(default_factory=list)
    rejected_urls: int = 0
    notes: list[str] = Field(default_factory=list)


class StoredWebsite(BaseModel):
    """A persisted website candidate."""

    website_id: UUID
    job_id: UUID
    candidate: WebsiteCandidate
    created_at: datetime


class MapWebsiteRequest(_Request):
    """Map a stored website's pages."""

    max_urls: int | None = Field(default=None, ge=1, le=5000)
    max_depth: int | None = Field(default=None, ge=0, le=6)
    search: str | None = Field(default=None, max_length=200)


class MapWebsiteResponse(BaseModel):
    """Mapped pages for a website."""

    website_id: UUID
    domain: str
    discovered_urls: int
    eligible_pages: int
    pages: list[MappedPage]


class CrawlWebsiteRequest(_Request):
    """Crawl selected pages of a mapped website."""

    max_pages: int | None = Field(default=None, ge=1, le=100)
    page_types: list[PageType] | None = None
    extract: bool = True


class CrawlWebsiteResponse(BaseModel):
    """Crawl (and optional extraction) outcome for a website."""

    website_id: UUID
    crawl: CrawlResult
    documents: int = 0
    entities: int = 0
    claims: int = 0
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)


class IntelligenceRequest(DiscoverWebsitesRequest):
    """Run the complete website intelligence workflow (synchronous)."""

    max_pages_per_site: int | None = Field(default=None, ge=1, le=100)
    report_type: ReportType | None = None
    website_urls: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Skip discovery and research these public URLs directly",
    )


class StageProgress(BaseModel):
    """Timing and outcome of one phase."""

    stage: str
    status: str
    duration_ms: int = 0
    item_count: int | None = None
    error: str | None = None


class IntelligenceResponse(BaseModel):
    """Result of a complete intelligence run."""

    job_id: UUID
    query: str
    status: IntelligenceStatus
    websites: int = 0
    mapped_pages: int = 0
    crawled_pages: int = 0
    failed_pages: int = 0
    documents: int = 0
    entities: int = 0
    claims: int = 0
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    stages: list[StageProgress] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    report_available: bool = False
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Durations (ms), counts and provider request counts for this run",
    )
    created_at: datetime
    completed_at: datetime | None = None


class Page[T](BaseModel):
    """Offset pagination wrapper."""

    items: list[T]
    total: int
    limit: int
    offset: int


class WebsitesPage(Page[StoredWebsite]):
    """Paginated websites."""


class EntitiesPage(Page[ResolvedEntity]):
    """Paginated entities."""


class ClaimsPage(Page[ResearchClaim]):
    """Paginated claims."""
