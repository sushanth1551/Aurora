"""Persistence for website intelligence (Module 3).

Extends the Module 2 tables without touching them destructively: new rows only,
plus status/count updates on the job the intelligence run owns. Methods commit
their own unit of work and translate database errors into
``PersistenceException`` with a safe message.
"""

import logging
from collections.abc import Sequence
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PersistenceException
from app.database.models import (
    MappedPageRecord,
    ProcessedDocumentRecord,
    ResearchClaimRecord,
    ResearchEntityRecord,
    ResearchJobRecord,
    ResearchReportRecord,
    ResearchSourceRecord,
    ResearchWebsiteRecord,
    SearchResultRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.schemas.intelligence import (
    ClaimsPage,
    EntitiesPage,
    EntityType,
    ExtractionMethod,
    GeneratedReport,
    IntelligenceStatus,
    MappedPage,
    PageType,
    ProcessedDocument,
    ReportType,
    ResearchClaim,
    ResearchReportData,
    ResolvedEntity,
    SourceKind,
    StoredWebsite,
    VerificationStatus,
    WebsiteCandidate,
    WebsitesPage,
)
from app.schemas.research import ResearchSource, SearchResult, utc_now

logger = logging.getLogger(__name__)

JOB_TYPE = "intelligence"


def _record_id(job_id: UUID, key: UUID) -> UUID:
    """Per-job primary key for an entity with a stable cross-run key."""
    return uuid5(NAMESPACE_URL, f"aurora:{job_id}:{key}")


class IntelligenceRepository:
    """Store and retrieve website-intelligence data using an injected session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- jobs ------------------------------------------------------------------

    async def create_job(
        self,
        job_id: UUID,
        query: str,
        max_websites: int,
        country: str | None,
        language: str | None,
    ) -> None:
        """Insert an intelligence job (``pending``)."""
        now = utc_now()
        self._session.add(
            ResearchJobRecord(
                id=job_id,
                job_type=JOB_TYPE,
                query=query[:500],
                status=IntelligenceStatus.PENDING.value,
                max_results=max_websites,
                scrape_pages=True,
                country=country,
                language=language,
                created_at=now,
                started_at=now,
                errors=[],
                stage_summaries=[],
            )
        )
        async with _translate_errors(self._session):
            await self._session.commit()

    async def get_job(self, job_id: UUID) -> ResearchJobRecord | None:
        """Return the job row for an intelligence job, else ``None``."""
        async with _translate_errors(self._session):
            record = await self._session.get(ResearchJobRecord, job_id)
        if record is None or record.job_type != JOB_TYPE:
            return None
        return record

    async def update_job(
        self,
        job_id: UUID,
        status: IntelligenceStatus,
        *,
        stages: list[dict[str, Any]] | None = None,
        errors: list[str] | None = None,
        counts: dict[str, int] | None = None,
        failure: tuple[str, str] | None = None,
        finished: bool = False,
    ) -> None:
        """Update status and (optionally) progress, counts and failure details."""
        async with _translate_errors(self._session):
            record = await self._session.get(ResearchJobRecord, job_id)
            if record is None:
                raise PersistenceException("Research job record is missing.")
            record.status = status.value
            if stages is not None:
                record.stage_summaries = stages
            if errors is not None:
                record.errors = [{"category": "error", "message": e} for e in errors]
            counts = counts or {}
            record.total_search_results = counts.get(
                "websites", record.total_search_results
            )
            record.eligible_source_count = counts.get(
                "pages", record.eligible_source_count
            )
            record.successful_source_count = counts.get(
                "ok", record.successful_source_count
            )
            record.failed_source_count = counts.get(
                "failed", record.failed_source_count
            )
            if failure:
                record.error_category, record.error_summary = failure
            if finished:
                record.completed_at = utc_now()
            await self._session.commit()

    # -- websites -----------------------------------------------------------------

    async def save_websites(
        self,
        job_id: UUID,
        candidates: Sequence[WebsiteCandidate],
        search_results: Sequence[SearchResult] = (),
    ) -> list[StoredWebsite]:
        """Insert discovered websites (and the raw search results) for a job."""
        now = utc_now()
        stored: list[StoredWebsite] = []
        async with _translate_errors(self._session):
            existing = {
                row
                for row in (
                    await self._session.execute(
                        select(ResearchWebsiteRecord.domain).where(
                            ResearchWebsiteRecord.research_job_id == job_id
                        )
                    )
                ).scalars()
            }
            for candidate in candidates:
                if candidate.domain in existing:
                    continue
                existing.add(candidate.domain)
                record = ResearchWebsiteRecord(
                    id=candidate.candidate_id,
                    research_job_id=job_id,
                    website_name=candidate.website_name[:200],
                    domain=candidate.domain,
                    homepage_url=candidate.homepage_url,
                    official_status=candidate.official_status.value,
                    website_type=candidate.website_type.value,
                    relevance_score=candidate.relevance_score,
                    discovery_metadata=candidate.model_dump(mode="json"),
                    created_at=now,
                )
                self._session.add(record)
                stored.append(
                    StoredWebsite(
                        website_id=candidate.candidate_id,
                        job_id=job_id,
                        candidate=candidate,
                        created_at=now,
                    )
                )
            for result in search_results:
                self._session.add(
                    SearchResultRecord(
                        research_job_id=job_id,
                        position=result.position,
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        displayed_link=result.displayed_link,
                        domain=result.domain,
                        provider_metadata=result.metadata,
                        created_at=now,
                    )
                )
            await self._session.commit()
        return stored

    @staticmethod
    def _stored_website(record: ResearchWebsiteRecord) -> StoredWebsite:
        return StoredWebsite(
            website_id=record.id,
            job_id=record.research_job_id,
            candidate=WebsiteCandidate.model_validate(record.discovery_metadata),
            created_at=record.created_at,
        )

    async def get_website(self, website_id: UUID) -> StoredWebsite | None:
        """Load one stored website."""
        async with _translate_errors(self._session):
            record = await self._session.get(ResearchWebsiteRecord, website_id)
        return self._stored_website(record) if record else None

    async def list_websites(
        self, job_id: UUID, limit: int = 50, offset: int = 0
    ) -> WebsitesPage:
        """Paginated websites for a job, best relevance first."""
        async with _translate_errors(self._session):
            total = (
                await self._session.execute(
                    select(func.count())
                    .select_from(ResearchWebsiteRecord)
                    .where(ResearchWebsiteRecord.research_job_id == job_id)
                )
            ).scalar_one()
            rows = (
                await self._session.execute(
                    select(ResearchWebsiteRecord)
                    .where(ResearchWebsiteRecord.research_job_id == job_id)
                    .order_by(
                        ResearchWebsiteRecord.relevance_score.desc(),
                        ResearchWebsiteRecord.domain,
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
            items = [self._stored_website(r) for r in rows]
        return WebsitesPage(items=items, total=total, limit=limit, offset=offset)

    async def update_website(
        self, website_id: UUID, candidate: WebsiteCandidate
    ) -> None:
        """Persist an updated candidate (e.g. official status confirmed)."""
        async with _translate_errors(self._session):
            record = await self._session.get(ResearchWebsiteRecord, website_id)
            if record is None:
                return
            record.official_status = candidate.official_status.value
            record.website_type = candidate.website_type.value
            record.discovery_metadata = candidate.model_dump(mode="json")
            await self._session.commit()

    # -- mapped pages ---------------------------------------------------------------

    async def save_mapped_pages(
        self, website_id: UUID, pages: Sequence[MappedPage]
    ) -> int:
        """Insert mapped pages not already stored; returns the number added."""
        added = 0
        async with _translate_errors(self._session):
            known = set(
                (
                    await self._session.execute(
                        select(MappedPageRecord.normalized_url).where(
                            MappedPageRecord.website_id == website_id
                        )
                    )
                ).scalars()
            )
            for page in pages:
                if page.normalized_url in known:
                    continue
                known.add(page.normalized_url)
                added += 1
                self._session.add(
                    MappedPageRecord(
                        id=uuid4(),
                        website_id=website_id,
                        url=page.url,
                        normalized_url=page.normalized_url,
                        title=page.title,
                        page_type=page.page_type.value,
                        relevance_score=page.relevance_score,
                        crawl_eligible=page.crawl_eligible,
                        crawl_status="pending" if page.crawl_eligible else "excluded",
                        exclusion_reason=page.exclusion_reason,
                        depth=page.depth,
                        discovery_method=page.discovery_method,
                        created_at=page.mapped_at,
                    )
                )
            await self._session.commit()
        return added

    async def list_mapped_pages(
        self, website_id: UUID, *, eligible_only: bool = False
    ) -> list[MappedPage]:
        """Mapped pages for a website, best relevance first."""
        query = select(MappedPageRecord).where(
            MappedPageRecord.website_id == website_id
        )
        if eligible_only:
            query = query.where(MappedPageRecord.crawl_eligible.is_(True))
        query = query.order_by(MappedPageRecord.relevance_score.desc())
        async with _translate_errors(self._session):
            rows = (await self._session.execute(query)).scalars().all()
        return [
            MappedPage(
                url=r.url,
                normalized_url=r.normalized_url,
                domain=r.normalized_url.split("/")[2]
                if "//" in r.normalized_url
                else "",
                title=r.title,
                page_type=PageType(r.page_type),
                relevance_score=r.relevance_score,
                discovery_method=r.discovery_method,
                mapped_at=r.created_at,
                crawl_eligible=r.crawl_eligible,
                exclusion_reason=r.exclusion_reason,
                depth=r.depth,
            )
            for r in rows
        ]

    async def mark_pages(self, website_id: UUID, statuses: dict[str, str]) -> None:
        """Set ``crawl_status`` for pages by normalized URL."""
        if not statuses:
            return
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(MappedPageRecord).where(
                        MappedPageRecord.website_id == website_id,
                        MappedPageRecord.normalized_url.in_(list(statuses)),
                    )
                )
            ).scalars()
            for row in rows:
                row.crawl_status = statuses[row.normalized_url]
            await self._session.commit()

    # -- site results ----------------------------------------------------------------

    async def save_site_result(
        self,
        job_id: UUID,
        sources: Sequence[ResearchSource],
        documents: Sequence[ProcessedDocument],
        entities: Sequence[ResolvedEntity],
        claims: Sequence[ResearchClaim],
    ) -> dict[str, int]:
        """Atomically store sources, documents, entities and claims for a website."""
        counts = {"sources": 0, "documents": 0, "entities": 0, "claims": 0}
        async with _translate_errors(self._session):
            known_urls = set(
                (
                    await self._session.execute(
                        select(ResearchSourceRecord.normalized_url).where(
                            ResearchSourceRecord.research_job_id == job_id
                        )
                    )
                ).scalars()
            )
            source_ids: dict[str, UUID] = {}
            for source in sources:
                if source.normalized_url in known_urls:
                    continue
                known_urls.add(source.normalized_url or "")
                source_ids[source.normalized_url or ""] = source.id
                counts["sources"] += 1
                self._session.add(
                    ResearchSourceRecord(
                        id=source.id,
                        research_job_id=job_id,
                        original_url=source.original_url,
                        normalized_url=source.normalized_url,
                        final_url=source.final_url,
                        domain=source.domain,
                        title=source.title,
                        snippet=source.snippet,
                        extracted_markdown=source.extracted_markdown,
                        extraction_status=source.extraction_status.value,
                        content_hash=source.content_hash,
                        provider_metadata=source.metadata,
                        error_message=source.error_message,
                        created_at=source.created_at,
                        extracted_at=source.extracted_at,
                    )
                )
            await self._session.flush()
            existing_sources = dict(
                (
                    await self._session.execute(
                        select(
                            ResearchSourceRecord.normalized_url, ResearchSourceRecord.id
                        ).where(ResearchSourceRecord.research_job_id == job_id)
                    )
                ).all()
            )
            for doc in documents:
                source_id = existing_sources.get(doc.normalized_url)
                if source_id is None:
                    continue  # a document must belong to a stored source
                self._session.add(
                    ProcessedDocumentRecord(
                        id=doc.document_id,
                        research_job_id=job_id,
                        source_id=source_id,
                        page_type=doc.page_type.value,
                        canonical_url=doc.canonical_url,
                        original_hash=doc.original_hash,
                        normalized_content=doc.normalized_markdown,
                        content_hash=doc.content_hash,
                        sections=[s.model_dump(mode="json") for s in doc.sections],
                        document_metadata=doc.metadata,
                        published_at=doc.published_at,
                        modified_at=doc.modified_at,
                        processing_status=doc.status.value,
                        duplicate_of=doc.duplicate_of,
                        created_at=doc.processed_at,
                    )
                )
                counts["documents"] += 1
            entity_rows = await self._save_entities(job_id, entities)
            counts["entities"] = len(entity_rows)
            await (
                self._session.flush()
            )  # entities before the claims that reference them
            for claim in claims:
                self._session.add(
                    ResearchClaimRecord(
                        id=claim.claim_id,
                        research_job_id=job_id,
                        entity_id=entity_rows.get(claim.entity_id)
                        if claim.entity_id
                        else None,
                        source_id=existing_sources.get(claim.source_url),
                        subject_entity=claim.subject_entity,
                        subject_type=claim.subject_type.value
                        if claim.subject_type
                        else None,
                        scope_domain=claim.scope_domain,
                        predicate=claim.predicate,
                        object_value=claim.object_value,
                        claim_text=claim.claim_text,
                        source_url=claim.source_url,
                        source_title=claim.source_title,
                        source_domain=claim.source_domain,
                        source_content_hash=claim.source_content_hash,
                        source_kind=claim.source_kind.value,
                        evidence_excerpt=claim.evidence_excerpt,
                        extraction_method=claim.extraction_method.value,
                        verification_status=claim.verification_status.value,
                        confidence=claim.confidence_score,
                        notes=claim.notes,
                        publication_date=claim.publication_date,
                        observed_at=claim.observed_at,
                    )
                )
                counts["claims"] += 1
            await self._session.commit()
        return counts

    async def _save_entities(
        self, job_id: UUID, entities: Sequence[ResolvedEntity]
    ) -> dict[UUID, UUID]:
        """Insert entities not yet stored for this job; map entity_key -> row id."""
        existing = dict(
            (
                await self._session.execute(
                    select(
                        ResearchEntityRecord.entity_key, ResearchEntityRecord.id
                    ).where(ResearchEntityRecord.research_job_id == job_id)
                )
            ).all()
        )
        mapping = dict(existing)
        for entity in entities:
            if entity.entity_id in existing:
                continue
            row_id = _record_id(job_id, entity.entity_id)
            mapping[entity.entity_id] = row_id
            self._session.add(
                ResearchEntityRecord(
                    id=row_id,
                    research_job_id=job_id,
                    entity_key=entity.entity_id,
                    entity_type=entity.entity_type.value,
                    canonical_name=entity.canonical_name,
                    normalized_name=entity.normalized_name[:300],
                    description=entity.description,
                    scope_domain=entity.scope_domain,
                    attributes=entity.attributes,
                    aliases=entity.aliases,
                    source_urls=entity.source_urls,
                    match_reasons=entity.match_reasons,
                    confidence=entity.confidence,
                    created_at=entity.created_at,
                )
            )
        return {
            k: v for k, v in mapping.items() if k in {e.entity_id for e in entities}
        }

    # -- reads ------------------------------------------------------------------------

    @staticmethod
    def _entity(r: ResearchEntityRecord) -> ResolvedEntity:
        return ResolvedEntity(
            entity_id=r.entity_key,
            entity_type=EntityType(r.entity_type),
            canonical_name=r.canonical_name,
            normalized_name=r.normalized_name,
            aliases=r.aliases or [],
            description=r.description,
            attributes=r.attributes or {},
            source_urls=r.source_urls or [],
            scope_domain=r.scope_domain,
            confidence=r.confidence,
            match_reasons=r.match_reasons or [],
            original_names=[],
            created_at=r.created_at,
        )

    async def list_entities(
        self,
        job_id: UUID,
        *,
        entity_type: EntityType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EntitiesPage:
        """Paginated entities for a job."""
        where = [ResearchEntityRecord.research_job_id == job_id]
        if entity_type:
            where.append(ResearchEntityRecord.entity_type == entity_type.value)
        async with _translate_errors(self._session):
            total = (
                await self._session.execute(
                    select(func.count()).select_from(ResearchEntityRecord).where(*where)
                )
            ).scalar_one()
            rows = (
                await self._session.execute(
                    select(ResearchEntityRecord)
                    .where(*where)
                    .order_by(
                        ResearchEntityRecord.entity_type,
                        ResearchEntityRecord.canonical_name,
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
            items = [self._entity(r) for r in rows]
        return EntitiesPage(items=items, total=total, limit=limit, offset=offset)

    async def list_claims(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        predicate: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ClaimsPage:
        """Paginated claims for a job."""
        where = [ResearchClaimRecord.research_job_id == job_id]
        if status:
            where.append(ResearchClaimRecord.verification_status == status)
        if predicate:
            where.append(ResearchClaimRecord.predicate == predicate)
        async with _translate_errors(self._session):
            total = (
                await self._session.execute(
                    select(func.count()).select_from(ResearchClaimRecord).where(*where)
                )
            ).scalar_one()
            rows = (
                await self._session.execute(
                    select(ResearchClaimRecord, ResearchEntityRecord.entity_key)
                    .join(
                        ResearchEntityRecord,
                        ResearchClaimRecord.entity_id == ResearchEntityRecord.id,
                        isouter=True,
                    )
                    .where(*where)
                    .order_by(ResearchClaimRecord.observed_at, ResearchClaimRecord.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            items = [self._claim(r, key) for r, key in rows]
        return ClaimsPage(items=items, total=total, limit=limit, offset=offset)

    @staticmethod
    def _claim(r: ResearchClaimRecord, entity_key: UUID | None) -> ResearchClaim:
        return ResearchClaim(
            claim_id=r.id,
            subject_entity=r.subject_entity,
            subject_type=EntityType(r.subject_type) if r.subject_type else None,
            scope_domain=r.scope_domain,
            entity_id=entity_key,
            predicate=r.predicate,
            object_value=r.object_value,
            claim_text=r.claim_text,
            source_url=r.source_url,
            source_title=r.source_title,
            source_domain=r.source_domain,
            source_content_hash=r.source_content_hash,
            source_kind=SourceKind(r.source_kind),
            evidence_excerpt=r.evidence_excerpt,
            extraction_method=ExtractionMethod(r.extraction_method),
            confidence_score=r.confidence,
            observed_at=r.observed_at,
            publication_date=r.publication_date,
            verification_status=VerificationStatus(r.verification_status),
            notes=r.notes or [],
        )

    async def load_material(
        self, job_id: UUID
    ) -> tuple[list[StoredWebsite], list[ResolvedEntity], list[ResearchClaim]]:
        """All websites, entities and claims of a job (for report regeneration)."""
        websites = (await self.list_websites(job_id, limit=1000)).items
        entities = (await self.list_entities(job_id, limit=100000)).items
        claims = (await self.list_claims(job_id, limit=100000)).items
        return websites, entities, claims

    async def pricing_pages_checked(self, job_id: UUID) -> dict[str, int]:
        """Number of processed pricing pages per registrable domain."""
        from app.research.domains import registrable_domain

        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(ResearchSourceRecord.domain, func.count())
                    .join(
                        ProcessedDocumentRecord,
                        ProcessedDocumentRecord.source_id == ResearchSourceRecord.id,
                    )
                    .where(
                        ProcessedDocumentRecord.research_job_id == job_id,
                        ProcessedDocumentRecord.page_type == PageType.PRICING.value,
                    )
                    .group_by(ResearchSourceRecord.domain)
                )
            ).all()
        result: dict[str, int] = {}
        for domain, count in rows:
            if domain:
                key = registrable_domain(domain)
                result[key] = result.get(key, 0) + count
        return result

    # -- reports ------------------------------------------------------------------------

    async def save_report(self, job_id: UUID, report: GeneratedReport) -> None:
        """Insert or replace the job's report of this type."""
        async with _translate_errors(self._session):
            record = (
                await self._session.execute(
                    select(ResearchReportRecord).where(
                        ResearchReportRecord.research_job_id == job_id,
                        ResearchReportRecord.report_type == report.report_type.value,
                    )
                )
            ).scalar_one_or_none()
            content = report.data.model_dump(mode="json")
            if record is None:
                self._session.add(
                    ResearchReportRecord(
                        id=report.report_id,
                        research_job_id=job_id,
                        report_type=report.report_type.value,
                        content=content,
                        markdown=report.markdown,
                        generated_at=report.generated_at,
                    )
                )
            else:
                record.content = content
                record.markdown = report.markdown
                record.generated_at = report.generated_at
            await self._session.commit()

    async def get_report(
        self, job_id: UUID, report_type: ReportType | None = None
    ) -> GeneratedReport | None:
        """Latest report for a job (optionally of a given type)."""
        query = select(ResearchReportRecord).where(
            ResearchReportRecord.research_job_id == job_id
        )
        if report_type:
            query = query.where(ResearchReportRecord.report_type == report_type.value)
        query = query.order_by(ResearchReportRecord.generated_at.desc()).limit(1)
        async with _translate_errors(self._session):
            record = (await self._session.execute(query)).scalar_one_or_none()
        if record is None:
            return None
        return GeneratedReport(
            report_id=record.id,
            report_type=ReportType(record.report_type),
            data=ResearchReportData.model_validate(record.content),
            markdown=record.markdown,
            generated_at=record.generated_at,
        )
