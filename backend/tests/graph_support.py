"""Builders for Module 5 tests: synthetic Module 3 records and wired services."""

from datetime import timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.database.repositories.graph_sync_repository import GraphSyncRepository
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.graph.graph_repository import InMemoryGraphRepository
from app.graph.graph_sync_service import GraphSyncService
from app.research.entity_resolver import entity_id_for, normalize_entity_name
from app.schemas.intelligence import (
    EntityType,
    ExtractionMethod,
    OfficialStatus,
    ResearchClaim,
    ResolvedEntity,
    SourceKind,
    StoredWebsite,
    VerificationStatus,
    WebsiteCandidate,
)
from app.schemas.research import ExtractionStatus, ResearchSource, utc_now


def entity(
    etype: EntityType, name: str, domain: str | None, *, aliases=None
) -> ResolvedEntity:
    scope = "*" if etype is EntityType.TECHNOLOGY or not domain else domain
    norm = normalize_entity_name(etype, name)
    return ResolvedEntity(
        entity_id=entity_id_for(etype, scope, norm),
        entity_type=etype,
        canonical_name=name,
        normalized_name=norm,
        aliases=list(aliases or []),
        scope_domain=None if scope == "*" else domain,
        source_urls=[f"https://{domain}/" if domain else "https://example.org/"],
        confidence=0.8,
    )


def claim(
    subject: ResolvedEntity,
    predicate: str,
    value: str,
    *,
    url: str | None = None,
    status: VerificationStatus = VerificationStatus.SUPPORTED,
    days_ago: int = 1,
) -> ResearchClaim:
    domain = subject.scope_domain or "example.org"
    url = url or f"https://{domain}/{predicate}"
    text = f"{subject.canonical_name} {predicate.replace('_', ' ')} {value}"
    return ResearchClaim(
        subject_entity=subject.canonical_name,
        subject_type=subject.entity_type,
        scope_domain=subject.scope_domain,
        entity_id=subject.entity_id,
        predicate=predicate,
        object_value=value,
        claim_text=text,
        source_url=url,
        source_title=f"{predicate} page",
        source_domain=domain,
        source_kind=SourceKind.OFFICIAL,
        evidence_excerpt=text,
        extraction_method=ExtractionMethod.DETERMINISTIC,
        confidence_score=0.7,
        observed_at=utc_now() - timedelta(days=days_ago),
        verification_status=status,
    )


def website(
    domain: str, *, verified: bool = True, job: UUID | None = None
) -> StoredWebsite:
    return StoredWebsite(
        website_id=uuid4(),
        job_id=job or uuid4(),
        candidate=WebsiteCandidate(
            website_name=domain.split(".")[0].title(),
            domain=domain,
            homepage_url=f"https://{domain}/",
            search_query="q",
            search_position=1,
            relevance_score=0.9,
            confidence=0.9,
            official_status=OfficialStatus.VERIFIED
            if verified
            else OfficialStatus.UNVERIFIED,
        ),
        created_at=utc_now(),
    )


def source(url: str, *, ok: bool = True) -> ResearchSource:
    return ResearchSource(
        original_url=url,
        normalized_url=url,
        final_url=url,
        domain=url.split("/")[2],
        title="Page",
        extracted_markdown="text" if ok else None,
        extraction_status=ExtractionStatus.SUCCEEDED if ok else ExtractionStatus.FAILED,
        content_hash="h" * 64 if ok else None,
        extracted_at=utc_now() if ok else None,
    )


def sync_service(
    session, graph=None, settings: Settings | None = None, sleep=None
) -> GraphSyncService:
    async def no_sleep(_: float) -> None:
        return None

    return GraphSyncService(
        research_repo=ResearchRepository(session),
        intelligence_repo=IntelligenceRepository(session),
        sync_repo=GraphSyncRepository(session),
        graph=graph if graph is not None else InMemoryGraphRepository(),
        settings=settings or Settings(),
        sleep=sleep or no_sleep,
    )


class FlakyGraph(InMemoryGraphRepository):
    """Fails the first ``failures`` writes with a transient graph error."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        super().__init__()
        self.failures = failures
        self.error = error
        self.attempts = 0

    async def upsert_nodes(self, label, rows):
        self.attempts += 1
        if self.failures > 0:
            self.failures -= 1
            from app.graph.graph_exceptions import GraphUnavailableError

            raise self.error or GraphUnavailableError("simulated outage")
        return await super().upsert_nodes(label, rows)
