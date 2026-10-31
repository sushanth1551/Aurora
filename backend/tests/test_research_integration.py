"""Integration tests.

* ``postgres``: run against a disposable PostgreSQL database named by
  ``AURORA_TEST_DATABASE_URL`` (e.g. ``postgresql+asyncpg://aurora:...@localhost:5432/aurora_test``).
  They apply the Alembic migration, run the full pipeline with mocked providers,
  and roll the schema back. NEVER point this at a database you care about.
* ``live``: call real SerpApi/Firecrawl; only when
  ``AURORA_RUN_LIVE_INTEGRATION_TESTS=true`` and keys are configured in ``.env``.
"""

import asyncio
import os
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import ENV_FILE, Settings, get_settings
from app.database.repositories.research_repository import ResearchRepository
from app.research.research_service import ResearchService
from app.research.source_manager import SourceManager
from app.schemas.research import ExtractionStatus, JobStatus, ResearchRequest
from tests.fakes import FakeExtractor, FakeSearch, failed_page, make_policy, result

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.environ.get("AURORA_TEST_DATABASE_URL")
LIVE = os.environ.get("AURORA_RUN_LIVE_INTEGRATION_TESTS", "false").lower() == "true"

_SAFE_DB_NAME = re.compile(r"^aurora_test_[a-z0-9_]+$")


def _unsafe_target(url: str | None) -> str | None:
    """Reason the URL must not be used for destructive tests, or ``None`` if safe.

    The fixtures run ``alembic downgrade base`` (dropping every application
    table), so only a database named ``aurora_test_<suffix>`` on this machine
    is accepted - never ``aurora``, ``aurora_test`` or ``aurora_live_test``.
    """
    if not url:
        return "AURORA_TEST_DATABASE_URL is not set"
    parsed = make_url(url)
    if parsed.host not in ("localhost", "127.0.0.1", "::1"):
        return "test database host is not local"
    if not _SAFE_DB_NAME.match(parsed.database or ""):
        return "test database name must match aurora_test_<suffix>"
    return None


UNSAFE_REASON = _unsafe_target(TEST_DB_URL)
requires_postgres = pytest.mark.skipif(
    UNSAFE_REASON is not None, reason=UNSAFE_REASON or ""
)
requires_live = pytest.mark.skipif(
    not LIVE, reason="AURORA_RUN_LIVE_INTEGRATION_TESTS is not 'true'"
)


SOURCE_1 = "00000000-0000-0000-0000-0000000000a1"
SOURCE_2 = "00000000-0000-0000-0000-0000000000a2"


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    return config


@pytest.fixture
async def migrated_db(monkeypatch):
    """Apply migrations to the test database, yield a session factory, roll back."""
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    config = alembic_config()
    # env.py uses asyncio.run(), so run Alembic in a worker thread.
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_async_engine(TEST_DB_URL)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), engine
    finally:
        await engine.dispose()
        await asyncio.to_thread(command.downgrade, config, "base")


@pytest.mark.postgres
@requires_postgres
async def test_migration_creates_and_removes_tables(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    config = alembic_config()
    engine = create_async_engine(TEST_DB_URL)
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert {"research_jobs", "search_results", "research_sources"} <= tables

        await asyncio.to_thread(command.downgrade, config, "base")
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert not {"research_jobs", "search_results", "research_sources"} & tables
    finally:
        await engine.dispose()


@pytest.mark.postgres
@requires_postgres
async def test_full_pipeline_with_mocked_providers(migrated_db) -> None:
    factory, _ = migrated_db
    urls = ["https://a.example.com/1", "https://b.example.com/2", "http://127.0.0.1/x"]
    search = FakeSearch(
        [result(i, u) for i, u in enumerate(urls, 1)] + [result(4, urls[0] + "#dup")]
    )
    extractor = FakeExtractor({urls[1]: failed_page(urls[1])})

    async with factory() as session:
        service = ResearchService(
            search=search,
            extractor=extractor,
            sources=SourceManager(make_policy()),
            repository=ResearchRepository(session),
            settings=Settings(),
        )
        response = await service.run(
            ResearchRequest(query="integration", request_id="it-key-0001")
        )

    assert response.status == JobStatus.PARTIALLY_COMPLETED
    assert (response.successful_source_count, response.failed_source_count) == (1, 1)

    async with factory() as session:  # fresh session: really read back from PostgreSQL
        stored = await ResearchService(
            search=search,
            extractor=extractor,
            sources=SourceManager(make_policy()),
            repository=ResearchRepository(session),
            settings=Settings(),
        ).get(response.job_id)
        again = await ResearchRepository(session).find_job_id_by_request_id(
            "it-key-0001"
        )

    assert again == response.job_id
    assert stored.status == JobStatus.PARTIALLY_COMPLETED
    by_status = {s.extraction_status for s in stored.sources}
    assert by_status == {
        ExtractionStatus.SUCCEEDED,
        ExtractionStatus.FAILED,
        ExtractionStatus.REJECTED,
    }
    assert len(stored.search_results) == 4  # every provider result is kept
    assert {s.normalized_url for s in stored.sources if s.normalized_url} == {
        urls[0],
        urls[1],
    }


@pytest.mark.postgres
@requires_postgres
async def test_unique_source_constraint_in_postgres(migrated_db) -> None:
    _, engine = migrated_db
    job_id = "00000000-0000-0000-0000-000000000001"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO research_jobs
                  (id, query, status, max_results, scrape_pages, created_at)
                VALUES (:id, 'q', 'running', 1, true, now())
                """
            ),
            {"id": job_id},
        )
    insert = text(
        """
        INSERT INTO research_sources
          (id, research_job_id, original_url, normalized_url,
           extraction_status, created_at)
        VALUES (:id, :job, 'u', 'https://x.com/', 'failed', now())
        """
    )
    async with engine.begin() as conn:
        await conn.execute(insert, {"id": SOURCE_1, "job": job_id})
    with pytest.raises(Exception, match="uq_source_job_normalized_url"):
        async with engine.begin() as conn:
            await conn.execute(insert, {"id": SOURCE_2, "job": job_id})


# -- optional live tests (disabled by default) ----------------------------------


@pytest.mark.live
@requires_live
async def test_live_serpapi_search() -> None:
    from app.tools.serpapi_tool import SerpApiTool

    settings = Settings(_env_file=ENV_FILE)
    if not settings.serpapi_api_key:
        pytest.skip("SERPAPI_API_KEY is not configured in .env")

    results = await SerpApiTool(settings).search(
        "python programming language", num_results=3
    )

    assert results and all(r.url.startswith("http") for r in results)


@pytest.mark.live
@requires_live
async def test_live_firecrawl_scrape() -> None:
    from app.tools.firecrawl_tool import FirecrawlTool

    settings = Settings(_env_file=ENV_FILE)
    if not settings.firecrawl_api_key:
        pytest.skip("FIRECRAWL_API_KEY is not configured in .env")

    page = await FirecrawlTool(settings).scrape_url("https://example.com/")

    assert (
        page.status == ExtractionStatus.SUCCEEDED and "Example Domain" in page.markdown
    )


# -- Module 3 -------------------------------------------------------------------


@pytest.mark.postgres
@requires_postgres
async def test_module3_migration_preserves_module2_rows(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    get_settings.cache_clear()
    config = alembic_config()
    engine = create_async_engine(TEST_DB_URL)
    job = "00000000-0000-0000-0000-0000000000b1"
    try:
        await asyncio.to_thread(command.upgrade, config, "0001")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO research_jobs (id, query, status, max_results,"
                    " scrape_pages, created_at) VALUES (:id, 'old job', 'completed',"
                    " 5, true, now())"
                ),
                {"id": job},
            )

        await asyncio.to_thread(command.upgrade, config, "head")
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT query, job_type FROM research_jobs WHERE id = :id"),
                    {"id": job},
                )
            ).one()
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert tuple(row) == (
            "old job",
            "research",
        )  # existing row kept, default applied
        assert {"research_websites", "research_claims", "research_reports"} <= tables

        await asyncio.to_thread(command.downgrade, config, "0001")
        async with engine.connect() as conn:
            count = (
                await conn.execute(text("SELECT count(*) FROM research_jobs"))
            ).scalar_one()
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert (
            count == 1
            and "research_claims" not in tables
            and "research_sources" in tables
        )
    finally:
        await asyncio.to_thread(command.downgrade, config, "base")
        await engine.dispose()


@pytest.mark.postgres
@requires_postgres
async def test_module3_pipeline_persists_in_postgres(migrated_db) -> None:
    from app.schemas.intelligence import (
        EntityType,
        IntelligenceRequest,
        IntelligenceStatus,
    )
    from tests.intel_support import (
        FakeCrawlProvider,
        FakeMapTool,
        acme_links,
        acme_pages,
        acme_search,
        build_service,
    )

    factory, _ = migrated_db
    async with factory() as session:
        service, _fakes = build_service(
            session,
            search=acme_search(),
            mapper=FakeMapTool(acme_links()),
            crawl=FakeCrawlProvider(acme_pages()),
        )
        summary = await service.run(
            IntelligenceRequest(query="Acme", target_names=["Acme"])
        )
    assert summary.status == IntelligenceStatus.COMPLETED

    async with factory() as session:  # fresh session: read back from PostgreSQL
        service, _fakes = build_service(session)
        company = await service.get_entities(summary.job_id, EntityType.COMPANY, 10, 0)
        claims = await service.get_claims(
            summary.job_id, "supported", "headquarters", 10, 0
        )
        report = await service.get_report(summary.job_id)
    assert company.items[0].attributes["headquarters"] == "Bengaluru, India"
    assert claims.items and claims.items[0].evidence_excerpt
    assert report.data.companies[0].pricing_status == "found"


@pytest.mark.live
@requires_live
async def test_live_website_intelligence_on_example_domain() -> None:
    """Real SerpApi-free run: Firecrawl maps and scrapes https://example.com/."""
    from app.research.intelligent_crawler import IntelligentCrawler
    from app.research.source_manager import SourceManager
    from app.research.url_policy import UrlPolicy
    from app.research.website_mapper import WebsiteMapper
    from app.tools.firecrawl_tool import FirecrawlTool

    settings = Settings(_env_file=ENV_FILE)
    if not settings.firecrawl_api_key:
        pytest.skip("FIRECRAWL_API_KEY is not configured in .env")
    sources = SourceManager(UrlPolicy())
    firecrawl = FirecrawlTool(settings)

    mapped = await WebsiteMapper(firecrawl, sources, settings=settings).map(
        "https://example.com/", "example", max_urls=10
    )
    crawl = await IntelligentCrawler(firecrawl, sources, settings).crawl(
        "example.com", mapped.pages, max_pages=2
    )

    assert mapped.pages and crawl.successful_pages >= 1
