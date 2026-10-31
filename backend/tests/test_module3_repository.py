"""Persistence tests for website intelligence (in-memory SQLite, FKs enforced)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import PersistenceException
from app.database.models import (
    ProcessedDocumentRecord,
    ResearchClaimRecord,
    ResearchEntityRecord,
    ResearchJobRecord,
    ResearchSourceRecord,
)
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.research.entity_resolver import EntityResolver
from app.research.evidence_manager import EvidenceManager
from app.research.research_report import ReportBuilder
from app.research.structured_extractor import DeterministicExtractor, WebsiteContext
from app.schemas.intelligence import (
    EntityType,
    IntelligenceStatus,
    MappedPage,
    OfficialStatus,
    PageType,
    ReportType,
    WebsiteCandidate,
)
from app.schemas.research import (
    ExtractionStatus,
    JobStatus,
    ResearchSource,
    SearchResult,
)
from tests.intel_support import HOME_MD, PRICING_MD, doc


def candidate(domain="acme.com", name="Acme", score=0.9) -> WebsiteCandidate:
    return WebsiteCandidate(
        website_name=name, domain=domain, homepage_url=f"https://{domain}/", search_query="q",
        search_position=1, relevance_score=score, official_status=OfficialStatus.VERIFIED, confidence=0.8,
    )  # fmt: skip


async def new_job(repo: IntelligenceRepository):
    job_id = uuid4()
    await repo.create_job(job_id, "Acme research", 5, "in", "en")
    return job_id


def source(url: str) -> ResearchSource:
    return ResearchSource(
        original_url=url, normalized_url=url, domain="acme.com", title="T",
        extracted_markdown="# md", extraction_status=ExtractionStatus.SUCCEEDED, content_hash="a" * 64,
    )  # fmt: skip


def material():
    """Documents, entities and (verified) claims for acme.com home + pricing pages."""
    docs = [
        doc("https://acme.com/", HOME_MD, PageType.HOMEPAGE),
        doc("https://acme.com/pricing", PRICING_MD, PageType.PRICING),
    ]
    ctx = WebsiteContext(
        "Acme", "acme.com", "https://acme.com/", OfficialStatus.VERIFIED
    )
    extracted = DeterministicExtractor().extract(ctx, docs)
    resolver = EntityResolver()
    entities = resolver.resolve(extracted.entities)
    claims, _ = EvidenceManager().validate(
        resolver.link_claims(extracted.claims, entities),
        docs,
        {"acme.com": OfficialStatus.VERIFIED},
    )
    return docs, entities, claims


async def test_job_lifecycle_and_type_isolation(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)

    job = await repo.get_job(job_id)
    assert (
        job.status == "pending"
        and job.job_type == "intelligence"
        and job.query == "Acme research"
    )

    await repo.update_job(
        job_id,
        IntelligenceStatus.COMPLETED,
        stages=[{"stage": "discovery", "status": "succeeded"}],
        errors=["one problem"],
        counts={"websites": 2, "pages": 9, "ok": 7, "failed": 2},
        finished=True,
    )
    job = await repo.get_job(job_id)
    assert (
        job.status == "completed" and job.completed_at and job.total_search_results == 2
    )
    assert job.eligible_source_count == 9 and job.errors[0]["message"] == "one problem"
    assert await repo.get_job(uuid4()) is None


async def test_module2_job_is_not_visible_as_intelligence_job(db_session) -> None:
    from app.schemas.research import ResearchJob, ResearchRequest

    research = ResearchRepository(db_session)
    request = ResearchRequest(query="q")
    job = ResearchJob(query="q", status=JobStatus.RUNNING, requested_result_count=5)
    await research.create_job(job, request)

    assert await IntelligenceRepository(db_session).get_job(job.job_id) is None
    record = await db_session.get(ResearchJobRecord, job.job_id)
    assert (
        record.job_type == "research"
    )  # Module 2 rows are untouched by the new column default


async def test_module2_get_handles_intelligence_statuses(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    await repo.update_job(job_id, IntelligenceStatus.CRAWLING)

    response = await ResearchRepository(db_session).get_response(job_id)

    assert response.status == JobStatus.RUNNING  # in-progress statuses map to 'running'


async def test_websites_roundtrip_pagination_and_uniqueness(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    sites = [
        candidate("acme.com", "Acme", 0.9),
        candidate("globex.com", "Globex", 0.8),
        candidate("acme.com", "Dup", 0.5),
    ]
    raw = [
        SearchResult(
            position=1,
            title="r",
            url="https://acme.com/",
            metadata={"search_query": "q"},
        )
    ]

    stored = await repo.save_websites(job_id, sites, raw)

    assert [s.candidate.domain for s in stored] == [
        "acme.com",
        "globex.com",
    ]  # duplicate domain skipped
    page = await repo.list_websites(job_id, limit=1, offset=0)
    assert (
        page.total == 2 and page.items[0].candidate.website_name == "Acme"
    )  # best relevance first
    second = await repo.list_websites(job_id, limit=1, offset=1)
    assert second.items[0].candidate.domain == "globex.com"
    loaded = await repo.get_website(stored[0].website_id)
    assert (
        loaded.candidate.official_status == OfficialStatus.VERIFIED
        and loaded.job_id == job_id
    )
    assert await repo.get_website(uuid4()) is None
    research = await ResearchRepository(db_session).get_response(job_id)
    assert len(research.search_results) == 1  # raw results go to the Module 2 table


async def test_update_website(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    (site,) = await repo.save_websites(job_id, [candidate()], [])
    changed = site.candidate.model_copy(
        update={"official_status": OfficialStatus.UNVERIFIED}
    )

    await repo.update_website(site.website_id, changed)

    assert (
        await repo.get_website(site.website_id)
    ).candidate.official_status == OfficialStatus.UNVERIFIED


async def test_mapped_pages_are_deduplicated_listed_and_marked(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    (site,) = await repo.save_websites(job_id, [candidate()], [])
    pages = [
        MappedPage(
            url="https://acme.com/pricing",
            normalized_url="https://acme.com/pricing",
            domain="acme.com",
            page_type=PageType.PRICING,
            relevance_score=0.9,
        ),
        MappedPage(
            url="https://acme.com/login",
            normalized_url="https://acme.com/login",
            domain="acme.com",
            crawl_eligible=False,
            exclusion_reason="authentication_page",
        ),
    ]

    assert await repo.save_mapped_pages(site.website_id, pages) == 2
    assert await repo.save_mapped_pages(site.website_id, pages) == 0  # idempotent

    eligible = await repo.list_mapped_pages(site.website_id, eligible_only=True)
    assert [p.normalized_url for p in eligible] == ["https://acme.com/pricing"]
    await repo.mark_pages(site.website_id, {"https://acme.com/pricing": "crawled"})
    all_pages = await repo.list_mapped_pages(site.website_id)
    assert len(all_pages) == 2 and all_pages[0].page_type == PageType.PRICING


async def test_site_result_links_documents_entities_and_claims(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    docs, entities, claims = material()

    counts = await repo.save_site_result(
        job_id, [source(d.normalized_url) for d in docs], docs, entities, claims
    )

    assert counts["sources"] == 2 and counts["documents"] == 2
    assert counts["entities"] == len(entities) and counts["claims"] == len(claims)
    claim_rows = (await db_session.execute(select(ResearchClaimRecord))).scalars().all()
    assert all(
        c.source_id is not None for c in claim_rows
    )  # every claim points at its stored source
    assert any(c.entity_id is not None for c in claim_rows)
    assert all(c.evidence_excerpt and c.source_url for c in claim_rows)
    docs_rows = (
        (await db_session.execute(select(ProcessedDocumentRecord))).scalars().all()
    )
    assert all(d.source_id for d in docs_rows) and {d.page_type for d in docs_rows} == {
        "homepage",
        "pricing",
    }

    again = await repo.save_site_result(
        job_id, [source(d.normalized_url) for d in docs], [], entities, []
    )
    assert (
        again["sources"] == 0
    )  # duplicate sources and entities are not inserted twice
    assert len(
        (await db_session.execute(select(ResearchEntityRecord))).scalars().all()
    ) == len(entities)


async def test_documents_without_a_stored_source_are_not_persisted(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    docs, entities, claims = material()

    counts = await repo.save_site_result(job_id, [], docs, [], [])

    assert counts["documents"] == 0


async def test_entities_and_claims_listing_filters_and_pagination(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    docs, entities, claims = material()
    await repo.save_site_result(
        job_id, [source(d.normalized_url) for d in docs], docs, entities, claims
    )

    everything = await repo.list_entities(job_id, limit=500)
    companies = await repo.list_entities(job_id, entity_type=EntityType.COMPANY)
    page = await repo.list_entities(job_id, limit=2, offset=0)

    assert everything.total == len(entities) and companies.total == 1
    assert companies.items[0].entity_id == next(
        e.entity_id for e in entities if e.entity_type == EntityType.COMPANY
    )
    assert len(page.items) == 2 and page.total == len(entities)
    prices = await repo.list_claims(job_id, predicate="price", limit=100)
    assert prices.total >= 2 and all(c.predicate == "price" for c in prices.items)
    supported = await repo.list_claims(job_id, status="supported", limit=500)
    assert supported.total >= 1 and all(
        c.verification_status.value == "supported" for c in supported.items
    )
    first = prices.items[0]
    assert (
        first.source_url.startswith("https://acme.com") and first.entity_id is not None
    )


async def test_load_material_and_pricing_pages_checked(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    await repo.save_websites(job_id, [candidate()], [])
    docs, entities, claims = material()
    await repo.save_site_result(
        job_id, [source(d.normalized_url) for d in docs], docs, entities, claims
    )

    websites, loaded_entities, loaded_claims = await repo.load_material(job_id)

    assert (
        len(websites) == 1
        and len(loaded_entities) == len(entities)
        and len(loaded_claims) == len(claims)
    )
    assert await repo.pricing_pages_checked(job_id) == {"acme.com": 1}


async def test_report_upsert_and_retrieval(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    docs, entities, claims = material()
    first = ReportBuilder().build(
        ReportType.COMPANY_PROFILE,
        "Acme",
        [candidate()],
        entities,
        claims,
        [],
        EvidenceManager.coverage(claims, entities),
    )
    second = ReportBuilder().build(
        ReportType.COMPANY_PROFILE,
        "Acme v2",
        [candidate()],
        entities,
        claims,
        [],
        EvidenceManager.coverage(claims, entities),
    )

    await repo.save_report(job_id, first)
    await repo.save_report(job_id, second)  # same type: replaced, not duplicated

    stored = await repo.get_report(job_id, ReportType.COMPANY_PROFILE)
    assert stored.data.subject == "Acme v2" and "Acme v2" in stored.markdown
    assert await repo.get_report(job_id, ReportType.INDUSTRY_REPORT) is None
    assert (await repo.get_report(job_id)).report_type == ReportType.COMPANY_PROFILE


async def test_deleting_a_job_cascades_to_all_module3_rows(db_session) -> None:
    repo = IntelligenceRepository(db_session)
    job_id = await new_job(repo)
    await repo.save_websites(job_id, [candidate()], [])
    docs, entities, claims = material()
    await repo.save_site_result(
        job_id, [source(d.normalized_url) for d in docs], docs, entities, claims
    )

    record = await db_session.get(ResearchJobRecord, job_id)
    await db_session.delete(record)
    await db_session.commit()

    for model in (
        ResearchSourceRecord,
        ProcessedDocumentRecord,
        ResearchEntityRecord,
        ResearchClaimRecord,
    ):
        assert (await db_session.execute(select(model))).scalars().all() == []


async def test_database_errors_are_safe(db_session) -> None:
    repo = IntelligenceRepository(db_session)

    async def broken(*_a, **_k):
        raise OSError("connection refused: host=db.internal password=hunter2")

    db_session.execute = broken  # type: ignore[method-assign]
    with pytest.raises(PersistenceException) as excinfo:
        await repo.list_websites(uuid4())

    assert str(
        excinfo.value
    ) == "The database is unavailable." and "hunter2" not in str(excinfo.value)


def test_utc_helpers_are_timezone_aware() -> None:
    assert candidate().discovery_timestamp.tzinfo is not None
    assert datetime.now(UTC).tzinfo is not None
