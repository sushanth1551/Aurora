"""Website intelligence service: the Module 3 pipeline orchestrator.

Coordinates discovery -> mapping -> crawling -> content processing ->
extraction -> entity resolution -> evidence validation -> reporting ->
persistence. It is a plain, reliable service pipeline (no autonomous agent) that
a LangGraph graph can wrap later. Websites are processed independently and
concurrently (bounded): a failure on one website is recorded and never stops
the others. Each website's results are persisted as soon as that website
finishes, so a deadline or crash keeps everything completed so far.

Execution is **synchronous**: ``run`` returns when the job finishes or the
configured deadline expires. Job status moves monotonically through
``discovering -> mapping -> crawling -> extracting -> validating -> reporting``
and ends as ``completed``, ``partially_completed`` or ``failed``.
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.core import metrics as run_metrics
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConfigurationException,
    ResearchException,
    ResourceNotFoundException,
)
from app.core.logging import log_event
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.research.content_processor import ContentProcessor, CrawledPage
from app.research.domains import domain_label, registrable_domain
from app.research.entity_resolver import EntityResolver
from app.research.evidence_manager import EvidenceManager
from app.research.intelligent_crawler import IntelligentCrawler
from app.research.research_report import ReportBuilder, default_report_type
from app.research.source_manager import SourceManager
from app.research.structured_extractor import StructuredExtractor, WebsiteContext
from app.research.url_policy import UrlRejected
from app.research.website_discovery import DiscoveryResult, WebsiteDiscovery
from app.research.website_mapper import WebsiteMapper
from app.schemas.intelligence import (
    ClaimConflict,
    ClaimsPage,
    CrawlResult,
    CrawlWebsiteRequest,
    CrawlWebsiteResponse,
    DiscoverWebsitesRequest,
    DocumentStatus,
    EntitiesPage,
    EntityType,
    EvidenceCoverage,
    GeneratedReport,
    IntelligenceRequest,
    IntelligenceResponse,
    IntelligenceStatus,
    MappedPage,
    MapWebsiteRequest,
    MapWebsiteResponse,
    OfficialStatus,
    PageType,
    ReportType,
    ResearchClaim,
    ResolvedEntity,
    StageProgress,
    StoredWebsite,
    VerificationStatus,
    WebsiteCandidate,
    WebsiteDiscoveryResponse,
    WebsitesPage,
    WebsiteType,
)
from app.schemas.research import ExtractionStatus, ResearchSource, utc_now
from app.tools.errors import ProviderError

logger = logging.getLogger(__name__)

_ORDER = [
    IntelligenceStatus.PENDING,
    IntelligenceStatus.DISCOVERING,
    IntelligenceStatus.MAPPING,
    IntelligenceStatus.CRAWLING,
    IntelligenceStatus.EXTRACTING,
    IntelligenceStatus.VALIDATING,
    IntelligenceStatus.REPORTING,
]
MAX_MAPPED_PAGES_RETURNED = 200


@dataclass
class SiteOutcome:
    """Everything produced for one website."""

    website: StoredWebsite
    pages: list[MappedPage] = field(default_factory=list)
    discovered_urls: int = 0
    crawl: CrawlResult | None = None
    documents: list[Any] = field(default_factory=list)
    entities: list[ResolvedEntity] = field(default_factory=list)
    claims: list[ResearchClaim] = field(default_factory=list)
    conflicts: list[ClaimConflict] = field(default_factory=list)
    pricing_pages: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def ok(self) -> bool:
        """True if at least one page was crawled successfully."""
        return bool(self.crawl and self.crawl.successful_pages)


@dataclass
class _Run:
    job_id: UUID
    status: IntelligenceStatus = IntelligenceStatus.PENDING
    started: float = field(default_factory=time.monotonic)
    stages: dict[str, StageProgress] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class WebsiteIntelligenceService:
    """Run the website intelligence pipeline with injected collaborators."""

    def __init__(
        self,
        *,
        discovery: WebsiteDiscovery,
        mapper: WebsiteMapper,
        crawler: IntelligentCrawler,
        sources: SourceManager,
        repository: IntelligenceRepository,
        processor: ContentProcessor | None = None,
        extractor: StructuredExtractor | None = None,
        resolver: EntityResolver | None = None,
        evidence: EvidenceManager | None = None,
        reports: ReportBuilder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._discovery = discovery
        self._mapper = mapper
        self._crawler = crawler
        self._sources = sources
        self._repo = repository
        self._processor = processor or ContentProcessor(self._settings)
        self._extractor = extractor or StructuredExtractor(settings=self._settings)
        self._resolver = resolver or EntityResolver()
        self._evidence = evidence or EvidenceManager()
        self._reports = reports or ReportBuilder()
        self._db = asyncio.Lock()  # one AsyncSession must not be used concurrently

    # -- staged endpoints ---------------------------------------------------------

    async def discover(
        self, request: DiscoverWebsitesRequest
    ) -> WebsiteDiscoveryResponse:
        """Discover and persist websites for a new job."""
        job_id = uuid4()
        limit = request.max_websites or self._settings.website_research_max_websites
        await self._repo.create_job(
            job_id, request.query, limit, request.country, request.language
        )
        await self._repo.update_job(job_id, IntelligenceStatus.DISCOVERING)
        try:
            found = await self._discovery.discover(request)
        except Exception as exc:
            await self._repo.update_job(
                job_id,
                IntelligenceStatus.FAILED,
                finished=True,
                failure=("discovery_failed", type(exc).__name__),
            )
            raise
        stored = await self._repo.save_websites(
            job_id, found.candidates, found.search_results
        )
        await self._repo.update_job(
            job_id,
            IntelligenceStatus.COMPLETED if stored else IntelligenceStatus.FAILED,
            counts={"websites": len(stored)},
            finished=True,
            failure=None
            if stored
            else ("no_websites", "no relevant websites were found"),
        )
        return WebsiteDiscoveryResponse(
            job_id=job_id,
            query=request.query,
            status=IntelligenceStatus.COMPLETED
            if stored
            else IntelligenceStatus.FAILED,
            websites=stored,
            rejected_urls=found.rejected_count,
            notes=found.notes,
        )

    async def map_website(
        self, website_id: UUID, request: MapWebsiteRequest
    ) -> MapWebsiteResponse:
        """Map a stored website and persist its pages."""
        website = await self._require_website(website_id)
        job = await self._repo.get_job(website.job_id)
        query = job.query if job else website.candidate.website_name
        result = await self._mapper.map(
            website.candidate.homepage_url,
            query,
            max_urls=request.max_urls,
            max_depth=request.max_depth,
            search=request.search,
            allowed_domains=[registrable_domain(website.candidate.domain)],
        )
        await self._repo.save_mapped_pages(website_id, result.pages)
        return MapWebsiteResponse(
            website_id=website_id,
            domain=website.candidate.domain,
            discovered_urls=result.discovered_urls,
            eligible_pages=len(result.eligible),
            pages=result.pages[:MAX_MAPPED_PAGES_RETURNED],
        )

    async def crawl_website(
        self, website_id: UUID, request: CrawlWebsiteRequest
    ) -> CrawlWebsiteResponse:
        """Crawl previously mapped pages, extract, validate and persist."""
        website = await self._require_website(website_id)
        pages = await self._repo.list_mapped_pages(website_id, eligible_only=True)
        if not pages:
            raise ResearchException("This website has no mapped pages; map it first.")
        job = await self._repo.get_job(website.job_id)
        query = job.query if job else website.candidate.website_name
        outcome = SiteOutcome(website=website, pages=pages)
        await self._crawl_extract_validate(
            outcome,
            query,
            max_pages=request.max_pages,
            page_types=request.page_types,
            extract=request.extract,
            seen=set(),
        )
        await self._persist_site(outcome)
        if self._settings.website_research_enable_reports and request.extract:
            await self._rebuild_report(website.job_id, query)
        coverage = self._evidence.coverage(outcome.claims, outcome.entities)
        return CrawlWebsiteResponse(
            website_id=website_id,
            crawl=outcome.crawl or CrawlResult(website=website.candidate.domain),
            documents=len(outcome.documents),
            entities=len(outcome.entities),
            claims=len(outcome.claims),
            coverage=coverage,
        )

    # -- full pipeline --------------------------------------------------------------

    async def run(self, request: IntelligenceRequest) -> IntelligenceResponse:
        """Run the complete workflow synchronously and return a summary."""
        cfg = self._settings
        job_id = uuid4()
        run = _Run(job_id)
        run_metrics.start_run(cfg.website_research_max_provider_requests)
        limit = min(
            request.max_websites or cfg.website_research_max_websites,
            cfg.website_research_max_websites,
        )
        await self._repo.create_job(
            job_id, request.query, limit, request.country, request.language
        )
        log_event(logger, "intelligence_started", research_job_id=job_id)
        outcomes: list[SiteOutcome] = []
        try:
            async with asyncio.timeout(cfg.website_research_timeout_seconds):
                websites = await self._discover_phase(run, request, limit)
                await self._site_phase(run, request, websites, outcomes)
                await self._report_phase(run, request)
        except TimeoutError:
            run.errors.append(
                "The research deadline was exceeded; results so far were kept."
            )
        except asyncio.CancelledError:
            run.errors.append("The job was cancelled.")
            await asyncio.shield(self._finish(run, outcomes, cancelled=True))
            raise
        except (ProviderError, ConfigurationException) as exc:
            run.errors.append(exc.message)
        except Exception as exc:
            logger.exception("intelligence_internal_error research_job_id=%s", job_id)
            run.errors.append(f"Unexpected error ({type(exc).__name__}).")
        return await self._finish(run, outcomes)

    async def _discover_phase(
        self, run: _Run, request: IntelligenceRequest, limit: int
    ) -> list[StoredWebsite]:
        await self._advance(run, IntelligenceStatus.DISCOVERING)
        started = time.monotonic()
        if request.website_urls:
            found = await self._from_urls(request, limit)
        else:
            found = await self._discovery.discover(request)
        stored = await self._locked(
            self._repo.save_websites(run.job_id, found.candidates, found.search_results)
        )
        self._stage(
            run,
            "discovery",
            "succeeded" if stored else "failed",
            started,
            len(stored),
            None if stored else "no relevant websites were found",
        )
        run.notes.extend(found.notes)
        return stored

    async def _site_phase(
        self,
        run: _Run,
        request: IntelligenceRequest,
        websites: list[StoredWebsite],
        outcomes: list[SiteOutcome],
    ) -> None:
        """Process websites concurrently; finished outcomes are appended to ``outcomes``
        immediately so a deadline still reports (and keeps) completed sites."""
        cfg = self._settings
        if not websites:
            return
        per_site = min(
            request.max_pages_per_site or cfg.website_research_max_pages_per_site,
            cfg.website_research_max_pages_per_site,
            max(1, cfg.website_research_max_total_pages // len(websites)),
        )
        gate = asyncio.Semaphore(cfg.website_research_max_concurrent_sites)
        seen: set[str] = set()

        async def one(site: StoredWebsite) -> SiteOutcome:
            outcome = SiteOutcome(website=site)
            async with gate:
                try:
                    await self._advance(run, IntelligenceStatus.MAPPING)
                    await self._map_site(outcome, request.query)
                    await self._crawl_extract_validate(
                        outcome,
                        request.query,
                        max_pages=per_site,
                        page_types=None,
                        extract=True,
                        seen=seen,
                        run=run,
                    )
                    await self._persist_site(outcome)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "site_failed domain=%s error=%s",
                        site.candidate.domain,
                        type(exc).__name__,
                    )
                    outcome.errors.append(
                        f"{site.candidate.domain}: {type(exc).__name__}: {str(exc)[:200]}"
                    )
            outcomes.append(outcome)
            return outcome

        await asyncio.gather(*(one(w) for w in websites))
        for stage in ("mapping", "crawling", "extraction", "validation", "persistence"):
            total = sum(o.timings.get(stage, 0) for o in outcomes)
            failed = [e for o in outcomes for e in o.errors]
            self._stage_ms(
                run,
                stage,
                "succeeded" if not failed else "partial",
                total,
                len(outcomes),
                None,
            )

    async def _report_phase(self, run: _Run, request: IntelligenceRequest) -> None:
        if not self._settings.website_research_enable_reports:
            return
        await self._advance(run, IntelligenceStatus.REPORTING)
        started = time.monotonic()
        await self._rebuild_report(
            run.job_id, request.query, request.report_type, request.subject_type.value
        )
        self._stage(run, "reporting", "succeeded", started, 1, None)

    async def _finish(
        self, run: _Run, outcomes: list[SiteOutcome], cancelled: bool = False
    ) -> IntelligenceResponse:
        site_errors = [e for o in outcomes for e in o.errors]
        run.errors.extend(site_errors)
        for note in dict.fromkeys(n for o in outcomes for n in o.notes):
            run.notes.append(note)
        any_ok = any(o.ok for o in outcomes)
        degraded = (
            bool(site_errors)
            or any((o.crawl and o.crawl.failed_pages) for o in outcomes)
            or any(not o.ok for o in outcomes)
        )
        deadline_or_cancel = cancelled or any(
            "deadline" in e or "cancelled" in e for e in run.errors
        )
        if not any_ok:
            status = IntelligenceStatus.FAILED
        elif degraded or deadline_or_cancel:
            status = IntelligenceStatus.PARTIALLY_COMPLETED
        else:
            status = IntelligenceStatus.COMPLETED
        pages = sum(len(o.pages) for o in outcomes)
        ok = sum(o.crawl.successful_pages for o in outcomes if o.crawl)
        failed = sum(o.crawl.failed_pages for o in outcomes if o.crawl)
        failure = None
        if status == IntelligenceStatus.FAILED:
            failure = (
                "intelligence_failed",
                (run.errors or ["no pages could be crawled"])[0][:300],
            )
        try:
            await self._repo.update_job(
                run.job_id,
                status,
                stages=[s.model_dump(mode="json") for s in run.stages.values()],
                errors=run.errors[:50],
                counts={
                    "websites": len(outcomes),
                    "pages": pages,
                    "ok": ok,
                    "failed": failed,
                },
                failure=failure,
                finished=True,
            )
        except Exception:
            logger.warning(
                "intelligence_finish_persist_failed research_job_id=%s", run.job_id
            )
        coverage = await self._job_coverage(run.job_id)
        job = await self._repo.get_job(run.job_id)
        log_event(
            logger,
            "intelligence_finished",
            research_job_id=run.job_id,
            status=status.value,
            sites=len(outcomes),
            pages_ok=ok,
            pages_failed=failed,
        )
        return IntelligenceResponse(
            job_id=run.job_id,
            query=job.query if job else "",
            status=status,
            websites=len(outcomes),
            mapped_pages=pages,
            crawled_pages=ok,
            failed_pages=failed,
            documents=sum(len(o.documents) for o in outcomes),
            entities=sum(len(o.entities) for o in outcomes),
            claims=sum(len(o.claims) for o in outcomes),
            coverage=coverage,
            stages=list(run.stages.values()),
            errors=run.errors[:50],
            notes=run.notes[:50],
            report_available=await self._repo.get_report(run.job_id) is not None,
            metrics=self._metrics(run, outcomes),
            created_at=job.created_at if job else utc_now(),
            completed_at=utc_now(),
        )

    @staticmethod
    def _metrics(run: _Run, outcomes: list[SiteOutcome]) -> dict[str, Any]:
        """Durations, counts and provider request counts for this run.

        Stage durations are summed over websites (sites run concurrently, so
        their sum can exceed ``total``); contains no URLs, queries or content.
        """
        current = run_metrics.current()
        data = current.snapshot() if current else {}
        durations: dict[str, int] = dict(data.get("durations_ms", {}))
        discovery = run.stages.get("discovery")
        if discovery:
            durations["discovery"] = discovery.duration_ms
        for stage in ("mapping", "crawling", "extraction", "validation", "persistence"):
            durations[stage] = sum(o.timings.get(stage, 0) for o in outcomes)
        durations["total"] = int((time.monotonic() - run.started) * 1000)
        counts: dict[str, int] = dict(data.get("counts", {}))
        counts["urls_discovered"] = sum(o.discovered_urls for o in outcomes)
        counts["urls_mapped_kept"] = sum(len(o.pages) for o in outcomes)
        counts["pages_scraped"] = sum(
            o.crawl.successful_pages for o in outcomes if o.crawl
        )
        counts["pages_failed"] = sum(o.crawl.failed_pages for o in outcomes if o.crawl)
        return {
            "durations_ms": dict(sorted(durations.items())),
            "counts": dict(sorted(counts.items())),
            "provider_requests": data.get("provider_requests", {}),
        }

    # -- per-site steps -----------------------------------------------------------------

    async def _map_site(self, outcome: SiteOutcome, query: str) -> None:
        started = time.monotonic()
        site = outcome.website.candidate
        result = await self._mapper.map(
            site.homepage_url, query, allowed_domains=[registrable_domain(site.domain)]
        )
        outcome.pages, outcome.discovered_urls = result.pages, result.discovered_urls
        await self._locked(
            self._repo.save_mapped_pages(outcome.website.website_id, result.pages)
        )
        outcome.timings["mapping"] += int((time.monotonic() - started) * 1000)

    async def _crawl_extract_validate(
        self,
        outcome: SiteOutcome,
        query: str,
        *,
        max_pages: int | None,
        page_types: list[PageType] | None,
        extract: bool,
        seen: set[str],
        run: _Run | None = None,
    ) -> None:
        site = outcome.website.candidate
        scope = [registrable_domain(site.domain)]
        if run:
            await self._advance(run, IntelligenceStatus.CRAWLING)
        started = time.monotonic()
        outcome.crawl = await self._crawler.crawl(
            site.domain,
            outcome.pages,
            max_pages=max_pages,
            page_types=page_types,
            allowed_domains=scope,
            seen_urls=seen,
        )
        outcome.timings["crawling"] += int((time.monotonic() - started) * 1000)
        type_of = {p.normalized_url: p.page_type for p in outcome.pages}
        items = [
            CrawledPage(
                r.normalized_url,
                type_of.get(r.normalized_url, PageType.UNKNOWN),
                r.page,
            )
            for r in outcome.crawl.page_results
            if r.status == "succeeded" and r.page is not None
        ]
        if run:
            await self._advance(run, IntelligenceStatus.EXTRACTING)
        started = time.monotonic()
        outcome.documents = self._processor.process(items)
        outcome.website = await self._confirm_official(outcome)
        if extract:
            ctx = WebsiteContext(
                name=outcome.website.candidate.website_name,
                domain=site.domain,
                homepage_url=site.homepage_url,
                official_status=outcome.website.candidate.official_status,
            )
            extracted = await self._extractor.extract(ctx, outcome.documents)
            outcome.entities = self._resolver.resolve(extracted.entities)
            outcome.claims = self._resolver.link_claims(
                extracted.claims, outcome.entities
            )
            outcome.pricing_pages = extracted.pricing_pages_checked
            for note in extracted.notes:
                (outcome.errors if "invalid" in note else outcome.notes).append(note)
        outcome.timings["extraction"] += int((time.monotonic() - started) * 1000)
        if run:
            await self._advance(run, IntelligenceStatus.VALIDATING)
        started = time.monotonic()
        status_map = {
            registrable_domain(site.domain): outcome.website.candidate.official_status
        }
        outcome.claims, outcome.conflicts = self._evidence.validate(
            outcome.claims,
            [d for d in outcome.documents if d.status == DocumentStatus.PROCESSED],
            status_map,
        )
        outcome.timings["validation"] += int((time.monotonic() - started) * 1000)
        if outcome.crawl.crawl_status.value == "failed":
            outcome.errors.append(f"{site.domain}: no page could be crawled")

    async def _confirm_official(self, outcome: SiteOutcome) -> StoredWebsite:
        site = outcome.website
        candidate = site.candidate
        home = next(
            (
                d
                for d in outcome.documents
                if d.page_type == PageType.HOMEPAGE and d.normalized_markdown
            ),
            None,
        )
        if home is None or candidate.official_status != OfficialStatus.UNVERIFIED:
            return site
        updated = self._discovery.confirm_with_homepage(
            candidate, home.title, home.normalized_markdown
        )
        if updated.official_status != candidate.official_status:
            await self._locked(self._repo.update_website(site.website_id, updated))
            return site.model_copy(update={"candidate": updated})
        return site

    async def _persist_site(self, outcome: SiteOutcome) -> None:
        started = time.monotonic()
        crawl = outcome.crawl
        sources = self._sources_from_crawl(crawl) if crawl else []
        await self._locked(
            self._repo.save_site_result(
                outcome.website.job_id,
                sources,
                outcome.documents,
                outcome.entities,
                outcome.claims,
            )
        )
        if crawl:
            statuses = {
                r.normalized_url: {
                    "succeeded": "crawled",
                    "failed": "failed",
                    "skipped": "skipped",
                }[r.status]
                for r in crawl.page_results
            }
            await self._locked(
                self._repo.mark_pages(outcome.website.website_id, statuses)
            )
        outcome.timings["persistence"] += int((time.monotonic() - started) * 1000)

    @staticmethod
    def _sources_from_crawl(crawl: CrawlResult) -> list[ResearchSource]:
        """One provenance record per attempted URL (kept even when it failed)."""
        sources: list[ResearchSource] = []
        for result in crawl.page_results:
            if result.error == "already_crawled":
                continue
            page = result.page
            ok = result.status == "succeeded" and page is not None
            status = (
                ExtractionStatus.SUCCEEDED
                if ok
                else ExtractionStatus.REJECTED
                if result.status == "skipped"
                else ExtractionStatus.FAILED
            )
            sources.append(
                ResearchSource(
                    original_url=result.url,
                    normalized_url=result.normalized_url,
                    domain=urlsplit(result.normalized_url).hostname,
                    title=page.title if page else None,
                    extracted_markdown=page.markdown if ok and page else None,
                    extraction_status=status,
                    content_hash=page.content_hash if ok and page else None,
                    metadata={
                        "method": result.method,
                        "attempts": result.attempts,
                        **(page.metadata if page else {}),
                    },
                    error_message=None if ok else result.error,
                    extracted_at=page.extracted_at if page else None,
                )
            )
        return sources

    # -- reports and reads ------------------------------------------------------------------

    async def _rebuild_report(
        self,
        job_id: UUID,
        query: str,
        report_type: ReportType | None = None,
        subject_type: str = "company",
    ) -> GeneratedReport:
        websites, entities, claims = await self._locked(
            self._repo.load_material(job_id)
        )
        candidates = [w.candidate for w in websites]
        if report_type is None:
            report_type = (
                default_report_type(subject_type)
                if len(candidates) <= 1 or subject_type != "company"
                else ReportType.COMPETITOR_PROFILE
            )
        conflicts = self._conflicts_from_claims(claims)
        coverage = self._evidence.coverage(claims, entities)
        checked = await self._locked(self._repo.pricing_pages_checked(job_id))
        report = self._reports.build(
            report_type,
            query,
            candidates,
            entities,
            claims,
            conflicts,
            coverage,
            checked,
        )
        await self._locked(self._repo.save_report(job_id, report))
        return report

    def _conflicts_from_claims(
        self, claims: list[ResearchClaim]
    ) -> list[ClaimConflict]:
        groups: dict[tuple[str, str], list[ResearchClaim]] = defaultdict(list)
        for claim in claims:
            if claim.verification_status == VerificationStatus.CONFLICTING:
                key = self._evidence.conflict_key(claim)
                if key:
                    groups[key].append(claim)
        return [
            ClaimConflict(
                subject_entity=g[0].subject_entity,
                predicate=g[0].predicate,
                values=sorted({c.object_value for c in g}),
                claim_ids=[c.claim_id for c in g],
            )
            for g in groups.values()
        ]

    async def _job_coverage(self, job_id: UUID) -> EvidenceCoverage:
        _, entities, claims = await self._locked(self._repo.load_material(job_id))
        return self._evidence.coverage(claims, entities)

    async def get_websites(self, job_id: UUID, limit: int, offset: int) -> WebsitesPage:
        """Paginated websites for a job (404 if the job is unknown)."""
        await self._require_job(job_id)
        return await self._repo.list_websites(job_id, limit, offset)

    async def get_entities(
        self, job_id: UUID, entity_type: EntityType | None, limit: int, offset: int
    ) -> EntitiesPage:
        """Paginated entities for a job."""
        await self._require_job(job_id)
        return await self._repo.list_entities(
            job_id, entity_type=entity_type, limit=limit, offset=offset
        )

    async def get_claims(
        self,
        job_id: UUID,
        status: str | None,
        predicate: str | None,
        limit: int,
        offset: int,
    ) -> ClaimsPage:
        """Paginated claims for a job."""
        await self._require_job(job_id)
        return await self._repo.list_claims(
            job_id, status=status, predicate=predicate, limit=limit, offset=offset
        )

    async def get_report(
        self, job_id: UUID, report_type: ReportType | None = None
    ) -> GeneratedReport:
        """The stored report, or 404."""
        await self._require_job(job_id)
        report = await self._repo.get_report(job_id, report_type)
        if report is None:
            raise ResourceNotFoundException(
                "No report has been generated for this job."
            )
        return report

    async def get_progress(self, job_id: UUID) -> IntelligenceResponse:
        """Job status, stage timings and counts."""
        job = await self._require_job(job_id)
        coverage = await self._job_coverage(job_id)
        websites = (await self._repo.list_websites(job_id, limit=1)).total
        return IntelligenceResponse(
            job_id=job_id,
            query=job.query,
            status=IntelligenceStatus(job.status),
            websites=websites,
            mapped_pages=job.eligible_source_count,
            crawled_pages=job.successful_source_count,
            failed_pages=job.failed_source_count,
            coverage=coverage,
            stages=[StageProgress.model_validate(s) for s in job.stage_summaries or []],
            errors=[e.get("message", "") for e in job.errors or []],
            report_available=await self._repo.get_report(job_id) is not None,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    # -- helpers --------------------------------------------------------------------------------

    async def _from_urls(
        self, request: IntelligenceRequest, limit: int
    ) -> DiscoveryResult:
        """Build candidates from user-supplied URLs (validated, never auto-verified)."""
        out = DiscoveryResult()
        seen: set[str] = set()
        for url in (request.website_urls or [])[:limit]:
            try:
                validated = await self._sources.policy.validate(url)
            except UrlRejected as exc:
                out.rejected_count += 1
                out.notes.append(f"URL rejected ({exc.reason}): {url[:100]}")
                continue
            parts = urlsplit(validated.normalized)
            domain = registrable_domain(validated.host)
            if domain in seen:
                continue
            seen.add(domain)
            out.candidates.append(
                WebsiteCandidate(
                    website_name=domain_label(validated.host).title(),
                    domain=domain,
                    homepage_url=f"{parts.scheme}://{parts.netloc}/",
                    search_query=request.query,
                    search_position=1,
                    relevance_score=1.0,
                    website_type=WebsiteType.UNKNOWN,
                    official_status=OfficialStatus.UNVERIFIED,
                    discovery_source="user_supplied",
                    confidence=0.5,
                    selection_reasons=["URL supplied by the user"],
                    metadata={"target_name": None},
                )
            )
        return out

    async def _require_website(self, website_id: UUID) -> StoredWebsite:
        website = await self._repo.get_website(website_id)
        if website is None:
            raise ResourceNotFoundException("Website not found.")
        return website

    async def _require_job(self, job_id: UUID) -> Any:
        job = await self._repo.get_job(job_id)
        if job is None:
            raise ResourceNotFoundException("Research job not found.")
        return job

    async def _locked(self, awaitable: Any) -> Any:
        async with self._db:
            return await awaitable

    async def _advance(self, run: _Run, status: IntelligenceStatus) -> None:
        if _ORDER.index(status) > _ORDER.index(run.status):
            run.status = status
            await self._locked(self._repo.update_job(run.job_id, status))

    @staticmethod
    def _stage(
        run: _Run,
        name: str,
        status: str,
        started: float,
        count: int | None,
        error: str | None,
    ) -> None:
        run.stages[name] = StageProgress(
            stage=name,
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            item_count=count,
            error=error,
        )

    @staticmethod
    def _stage_ms(
        run: _Run, name: str, status: str, ms: int, count: int, error: str | None
    ) -> None:
        run.stages[name] = StageProgress(
            stage=name, status=status, duration_ms=ms, item_count=count, error=error
        )
