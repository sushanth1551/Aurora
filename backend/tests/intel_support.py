"""Shared doubles and sample content for Module 3 tests (no network, no keys)."""

from collections.abc import Callable

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.hashing import content_hash
from app.database.models import Base
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.research.content_processor import ContentProcessor, CrawledPage
from app.research.intelligent_crawler import IntelligentCrawler
from app.research.source_manager import SourceManager
from app.research.website_discovery import WebsiteDiscovery
from app.research.website_intelligence_service import WebsiteIntelligenceService
from app.research.website_mapper import WebsiteMapper
from app.schemas.intelligence import PageType, ProcessedDocument
from app.schemas.research import ExtractedPage, ExtractionStatus, SearchResult
from app.tools.errors import ProviderError, ProviderUnavailableError
from app.tools.firecrawl_tool import MapLink
from tests.fakes import make_policy

HOME_MD = """# Acme Automation

Acme Automation is an AI automation company that helps businesses automate workflows.

We are headquartered in Bengaluru, India. Founded in 2016, Acme has 250 employees.

Our platform is built for enterprises and startups. It works with Python and Kubernetes.
"""

ABOUT_MD = """# About Acme

Acme was established in 2016 to make automation simple for every team.
"""

PRICING_MD = """# Pricing

## Starter

$29 per month per user. Start with a 14-day free trial.

## Pro

**Pro** $99 per month billed monthly.

## Enterprise

Contact sales for custom pricing.
"""

PRODUCT_MD = """# Acme Flow

Acme Flow lets teams build automated workflows without writing code.

## Key features

- Visual workflow builder
- Prebuilt connectors

## Integrations

- Slack
- Salesforce

## Use cases

- Invoice processing
"""

SITE = "https://acme.com"
URLS = {
    "home": f"{SITE}/",
    "about": f"{SITE}/about",
    "pricing": f"{SITE}/pricing",
    "product": f"{SITE}/product/flow",
}
CONTENT = {
    "home": HOME_MD,
    "about": ABOUT_MD,
    "pricing": PRICING_MD,
    "product": PRODUCT_MD,
}


def page(url: str, markdown: str, title: str | None = None, **meta) -> ExtractedPage:
    """A successfully extracted page."""
    return ExtractedPage(
        original_url=url,
        title=title,
        markdown=markdown,
        status=ExtractionStatus.SUCCEEDED,
        content_hash=content_hash(markdown),
        metadata=dict(meta),
    )


def bad_page(url: str, category: str = "provider_unavailable") -> ExtractedPage:
    """A failed extraction."""
    return ExtractedPage(
        original_url=url,
        status=ExtractionStatus.FAILED,
        error_message="boom",
        metadata={"error_category": category},
    )


def doc(
    url: str, markdown: str, page_type: PageType, title: str | None = None
) -> ProcessedDocument:
    """A processed document built through the real processor."""
    processor = ContentProcessor(Settings())
    return processor.process([CrawledPage(url, page_type, page(url, markdown, title))])[
        0
    ]


def result(position: int, url: str, title: str = "", snippet: str = "") -> SearchResult:
    """A search result."""
    return SearchResult(position=position, title=title, url=url, snippet=snippet)


class QuerySearch:
    """Search double: returns results whose key occurs in the query."""

    def __init__(
        self,
        mapping: dict[str, list[SearchResult]] | None = None,
        error: Exception | None = None,
    ):
        self.mapping = mapping or {}
        self.error = error
        self.calls: list[dict] = []

    async def search(self, query, *, num_results=10, country=None, language=None):
        self.calls.append({"query": query, "country": country, "language": language})
        if self.error:
            raise self.error
        for key, results in self.mapping.items():
            if key.lower() in query.lower():
                return [r.model_copy(deep=True) for r in results]
        return []


class FakeMapTool:
    """Map double keyed by site URL."""

    def __init__(
        self,
        links: dict[str, list[MapLink | str]] | None = None,
        error: Exception | None = None,
    ):
        self.links = links or {}
        self.error = error
        self.calls: list[dict] = []

    async def map_site(self, url, *, search=None, limit=200, include_subdomains=False):
        self.calls.append({"url": url, "limit": limit, "search": search})
        if self.error:
            raise self.error
        items = self.links.get(url, self.links.get("*", []))
        return [MapLink(url=i) if isinstance(i, str) else i for i in items][:limit]


class FakeCrawlProvider:
    """Crawl double. ``pages`` maps URL -> ExtractedPage (default: generic success)."""

    def __init__(
        self,
        pages: dict[str, ExtractedPage] | None = None,
        batch_error: Exception | None = None,
    ):
        self.pages = pages or {}
        self.batch_error = batch_error
        self.batch_calls: list[list[str]] = []
        self.scrape_calls: list[list[str]] = []
        self.scrape_overrides: dict[str, ExtractedPage] = {}

    def _page(self, url: str) -> ExtractedPage:
        return self.pages.get(url) or page(
            url, f"# Page\n\nGeneric content for {url} that is long enough.", "Page"
        )

    async def batch_scrape(self, urls):
        self.batch_calls.append(list(urls))
        if self.batch_error:
            raise self.batch_error
        return [self._page(u) for u in urls]

    async def scrape_many(self, urls, *, concurrency=None):
        self.scrape_calls.append(list(urls))
        return [self.scrape_overrides.get(u) or self._page(u) for u in urls]


class FlakyBatch(FakeCrawlProvider):
    """Batch returns transient failures for some URLs; individual scraping succeeds."""

    def __init__(self, flaky: set[str], **kwargs):
        super().__init__(**kwargs)
        self.flaky = flaky

    async def batch_scrape(self, urls):
        self.batch_calls.append(list(urls))
        return [
            bad_page(u, "provider_unavailable") if u in self.flaky else self._page(u)
            for u in urls
        ]


def unavailable() -> ProviderError:
    """A transient provider error."""
    return ProviderUnavailableError("Firecrawl", "down")


@pytest.fixture
async def db_session():
    """In-memory SQLite session with the real ORM schema (FKs enforced)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def build_service(
    session: AsyncSession,
    *,
    search=None,
    mapper=None,
    crawl=None,
    dns: dict[str, list[str]] | None = None,
    settings: Settings | None = None,
    robots=None,
) -> tuple[WebsiteIntelligenceService, dict]:
    """Assemble a service over fakes and the SQLite repository."""
    settings = settings or Settings(website_research_respect_robots=False)
    sources = SourceManager(make_policy(dns))
    search = search or QuerySearch()
    mapper = mapper or FakeMapTool()
    crawl = crawl or FakeCrawlProvider()
    service = WebsiteIntelligenceService(
        discovery=WebsiteDiscovery(search, sources, settings),
        mapper=WebsiteMapper(mapper, sources, settings=settings),
        crawler=IntelligentCrawler(crawl, sources, settings, robots),
        sources=sources,
        repository=IntelligenceRepository(session),
        settings=settings,
    )
    return service, {
        "search": search,
        "mapper": mapper,
        "crawl": crawl,
        "sources": sources,
    }


def acme_search() -> QuerySearch:
    """Search results for an 'Acme' company lookup (official site + a review site)."""
    return QuerySearch(
        {
            "Acme": [
                result(
                    1,
                    "https://g2.com/products/acme",
                    "Acme Reviews | G2",
                    "Reviews of Acme",
                ),
                result(
                    2,
                    "https://acme.com/",
                    "Acme Automation - Official Site",
                    "Acme automates workflows",
                ),
                result(
                    3,
                    "https://acme.com/pricing",
                    "Pricing | Acme",
                    "Acme pricing plans",
                ),
            ]
        }
    )


def acme_links() -> dict[str, list]:
    """Map output for acme.com including junk that must be excluded."""
    return {
        "https://acme.com/": [
            URLS["about"],
            URLS["pricing"],
            URLS["product"],
            f"{SITE}/login",
            f"{SITE}/logo.png",
            "https://other-site.com/page",
            "http://10.0.0.5/admin",
        ]
    }


def acme_pages() -> dict[str, ExtractedPage]:
    """Scrape output for the acme.com pages."""
    return {
        "https://acme.com/": page(
            "https://acme.com/", HOME_MD, "Acme Automation | Home"
        ),
        URLS["about"]: page(URLS["about"], ABOUT_MD, "About Acme"),
        URLS["pricing"]: page(URLS["pricing"], PRICING_MD, "Pricing | Acme"),
        URLS["product"]: page(URLS["product"], PRODUCT_MD, "Acme Flow"),
    }


Factory = Callable[..., WebsiteIntelligenceService]
