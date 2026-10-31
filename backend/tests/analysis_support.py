"""Shared helpers for Module 4 tests (no network, no keys, no paid LLM calls)."""

from datetime import timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.database.repositories.analysis_repository import AnalysisRepository
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.intelligence.context_builder import ContextBuilder
from app.intelligence.models import (
    EvidenceItem,
    EvidenceKind,
    SourceCredibility,
    SourceInfo,
    Tier,
    UnifiedResearchContext,
)
from app.intelligence.service import IntelligenceAnalysisService
from app.llm.mock_provider import MockProvider
from app.schemas.intelligence import (
    EntityType,
    IntelligenceRequest,
    SourceKind,
    VerificationStatus,
)
from app.schemas.research import utc_now
from tests.intel_support import (
    FakeCrawlProvider,
    FakeMapTool,
    acme_links,
    acme_pages,
    acme_search,
    build_service,
)

JOB = uuid4()


async def seed_acme_job(session) -> UUID:
    """Run the real Module 3 pipeline over fakes; returns the stored job id."""
    service, _ = build_service(
        session,
        search=acme_search(),
        mapper=FakeMapTool(acme_links()),
        crawl=FakeCrawlProvider(acme_pages()),
    )
    response = await service.run(
        IntelligenceRequest(query="Acme", target_names=["Acme"])
    )
    return response.job_id


def builder(session, settings: Settings | None = None) -> ContextBuilder:
    return ContextBuilder(
        ResearchRepository(session),
        IntelligenceRepository(session),
        settings=settings or Settings(),
    )


def analysis_service(
    session, provider=None, settings: Settings | None = None, executor=None
) -> IntelligenceAnalysisService:
    settings = settings or Settings()
    provider = provider or MockProvider(settings)

    def provider_builder(*_args, **_kwargs):
        return provider

    return IntelligenceAnalysisService(
        repository=AnalysisRepository(session),
        builder=builder(session, settings),
        executor=executor,
        provider_builder=provider_builder,
        settings=settings,
    )


def ev(
    eid: str,
    entity: str,
    predicate: str,
    value: str,
    *,
    domain: str = "acme.com",
    status: VerificationStatus = VerificationStatus.SUPPORTED,
    entity_type: EntityType = EntityType.COMPANY,
    job: UUID = JOB,
    kind: SourceKind = SourceKind.OFFICIAL,
    url: str | None = None,
) -> EvidenceItem:
    """A synthetic claim evidence item; its excerpt is its text."""
    text = f"{entity} {predicate.replace('_', ' ')} {value}"
    return EvidenceItem(
        evidence_id=eid,
        kind=EvidenceKind.CLAIM,
        research_job_id=job,
        claim_id=uuid4(),
        entity=entity,
        entity_type=entity_type,
        predicate=predicate,
        value=value,
        text=text,
        excerpt=text,
        source_url=url or f"https://{domain}/{predicate}",
        source_domain=domain,
        source_kind=kind,
        verification_status=status,
        extraction_confidence=0.7,
        observed_at=utc_now() - timedelta(days=3),
        credibility=SourceCredibility(score=0.75, tier=Tier.OFFICIAL_SITE),
    )


def context_of(
    items: list[EvidenceItem], objective: str = "Compare AI automation companies"
) -> UnifiedResearchContext:
    """A context whose sources and texts match the evidence exactly."""
    sources: dict[str, SourceInfo] = {}
    for i in items:
        sources.setdefault(
            i.source_url,
            SourceInfo(
                research_job_id=i.research_job_id,
                url=i.source_url,
                domain=i.source_domain,
                kind=i.source_kind,
                has_full_text=True,
            ),
        ).evidence_ids.append(i.evidence_id)
    texts: dict[str, str] = {}
    for i in items:
        texts[i.source_url] = texts.get(i.source_url, "") + "\n" + i.excerpt
    return UnifiedResearchContext(
        research_job_id=JOB,
        job_ids=[JOB],
        objective=objective,
        companies=sorted(
            {
                i.entity
                for i in items
                if i.entity_type is EntityType.COMPANY and i.entity
            }
        ),
        evidence=items,
        sources=list(sources.values()),
        verified_ids=[
            i.evidence_id
            for i in items
            if i.verification_status is VerificationStatus.SUPPORTED
        ],
        source_texts=texts,
    )


def two_company_evidence() -> list[EvidenceItem]:
    return [
        ev("c-a1", "Acme", "offers_product", "Acme Flow"),
        ev("c-a2", "Acme", "price", "$29 per month"),
        ev("c-a3", "Acme", "integration", "Slack"),
        ev("c-a4", "Acme", "target_customer", "enterprises"),
        ev("c-b1", "Beta", "offers_product", "Beta Bots", domain="beta.io"),
        ev("c-b2", "Beta", "integration", "Slack", domain="beta.io"),
        ev("c-b3", "Beta", "integration", "Zapier", domain="beta.io"),
        ev("c-b4", "Beta", "headquarters", "Pune", domain="beta.io"),
        ev(
            "c-b5",
            "Beta",
            "headquarters",
            "Mumbai",
            domain="beta.io",
            url="https://beta.io/about",
            status=VerificationStatus.CONFLICTING,
        ),
    ]
