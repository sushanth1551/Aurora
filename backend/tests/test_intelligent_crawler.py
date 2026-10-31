"""Tests for page selection and the bounded, audited crawler."""

from app.core.config import Settings
from app.research.intelligent_crawler import (
    FALLBACK_PAGE_LIMIT,
    IntelligentCrawler,
    select_pages,
)
from app.research.robots import RobotsChecker
from app.research.source_manager import SourceManager
from app.schemas.intelligence import CrawlStatus, MappedPage, PageType
from app.tools.errors import ProviderAuthenticationError
from tests.fakes import make_policy
from tests.intel_support import (
    SITE,
    FakeCrawlProvider,
    FlakyBatch,
    bad_page,
    unavailable,
)


def mp(
    path: str, page_type: PageType, score: float, eligible: bool = True
) -> MappedPage:
    url = f"{SITE}{path}"
    return MappedPage(
        url=url, normalized_url=url, domain="acme.com", page_type=page_type,
        relevance_score=score, crawl_eligible=eligible,
        exclusion_reason=None if eligible else "authentication_page",
    )  # fmt: skip


def crawler(provider, dns=None, robots=None, **settings) -> IntelligentCrawler:
    sources = SourceManager(make_policy(dns))
    return IntelligentCrawler(provider, sources, Settings(**settings), robots)


MAPPED = [
    mp("/", PageType.HOMEPAGE, 0.8),
    mp("/blog/a", PageType.BLOG, 0.35),
    mp("/blog/b", PageType.BLOG, 0.34),
    mp("/pricing", PageType.PRICING, 0.95),
    mp("/product/x", PageType.PRODUCT, 0.9),
    mp("/careers", PageType.CAREERS, 0.1),
    mp("/login", PageType.UNKNOWN, 0.0, eligible=False),
]


def test_selection_prioritizes_important_page_types() -> None:
    chosen = select_pages(MAPPED, 3)

    assert {p.page_type for p in chosen} == {
        PageType.PRICING,
        PageType.PRODUCT,
        PageType.HOMEPAGE,
    }


def test_selection_respects_budget_filters_and_eligibility() -> None:
    assert len(select_pages(MAPPED, 100)) == 6  # the excluded login page never appears
    assert [p.page_type for p in select_pages(MAPPED, 5, [PageType.BLOG])] == [
        PageType.BLOG
    ] * 2
    assert select_pages(MAPPED, 1)[0].page_type == PageType.HOMEPAGE


async def test_crawl_scrapes_selected_pages_in_one_batch() -> None:
    provider = FakeCrawlProvider()

    result = await crawler(provider).crawl("acme.com", MAPPED, max_pages=3)

    assert len(provider.batch_calls) == 1 and len(provider.batch_calls[0]) == 3
    assert provider.scrape_calls == []
    assert (result.requested_pages, result.successful_pages, result.failed_pages) == (
        3,
        3,
        0,
    )
    assert result.crawl_status == CrawlStatus.COMPLETED and result.completed_at
    assert {a.action for a in result.audit} >= {"selected", "scraped"}


async def test_page_limit_is_capped_by_configuration() -> None:
    provider = FakeCrawlProvider()

    result = await crawler(provider, website_research_max_pages_per_site=2).crawl(
        "acme.com", MAPPED, max_pages=50
    )

    assert result.requested_pages == 2 and len(provider.batch_calls[0]) == 2


async def test_partial_failure_is_partial_not_failed() -> None:
    provider = FakeCrawlProvider(
        {f"{SITE}/pricing": bad_page(f"{SITE}/pricing", "empty_content")}
    )

    result = await crawler(provider).crawl("acme.com", MAPPED, max_pages=3)

    assert result.crawl_status == CrawlStatus.PARTIAL
    assert (result.successful_pages, result.failed_pages) == (2, 1)
    failed = next(r for r in result.page_results if r.status == "failed")
    assert failed.error and failed.page is not None


async def test_everything_failing_is_failed() -> None:
    provider = FakeCrawlProvider(
        {m.normalized_url: bad_page(m.normalized_url, "empty_content") for m in MAPPED}
    )

    result = await crawler(provider).crawl("acme.com", MAPPED, max_pages=3)

    assert result.crawl_status == CrawlStatus.FAILED


async def test_transient_failures_are_retried_once_individually() -> None:
    flaky = {f"{SITE}/pricing"}
    provider = FlakyBatch(flaky)

    result = await crawler(provider).crawl("acme.com", MAPPED, max_pages=3)

    assert provider.scrape_calls == [[f"{SITE}/pricing"]]  # one bounded retry round
    retried = next(
        r for r in result.page_results if r.normalized_url == f"{SITE}/pricing"
    )
    assert retried.status == "succeeded" and retried.attempts == 2
    assert result.crawl_status == CrawlStatus.COMPLETED
    assert any(a.action == "retried" for a in result.audit)


async def test_batch_failure_falls_back_to_limited_individual_scraping() -> None:
    many = [
        mp(f"/p{i}", PageType.UNKNOWN, 0.5 - i / 100)
        for i in range(FALLBACK_PAGE_LIMIT + 3)
    ]
    provider = FakeCrawlProvider(batch_error=unavailable())

    result = await crawler(provider, website_research_max_pages_per_site=50).crawl(
        "acme.com", many, max_pages=50
    )

    assert len(provider.scrape_calls[0]) == FALLBACK_PAGE_LIMIT
    assert result.successful_pages == FALLBACK_PAGE_LIMIT and result.failed_pages == 3
    assert any("falling back" in e for e in result.errors)
    assert {r.method for r in result.page_results if r.status == "succeeded"} == {
        "scrape"
    }


async def test_authentication_failure_does_not_fall_back() -> None:
    provider = FakeCrawlProvider(
        batch_error=ProviderAuthenticationError("Firecrawl", "bad key")
    )

    result = await crawler(provider).crawl("acme.com", MAPPED, max_pages=3)

    assert provider.scrape_calls == []
    assert result.crawl_status == CrawlStatus.FAILED
    assert any("permanently" in e for e in result.errors)


async def test_already_crawled_urls_are_not_scraped_again() -> None:
    provider = FakeCrawlProvider()
    seen = {f"{SITE}/pricing"}

    result = await crawler(provider).crawl(
        "acme.com", MAPPED, max_pages=3, seen_urls=seen
    )

    assert f"{SITE}/pricing" not in provider.batch_calls[0]
    assert result.skipped_pages == 1
    assert (
        next(r for r in result.page_results if r.status == "skipped").error
        == "already_crawled"
    )
    assert f"{SITE}/pricing" in seen


async def test_urls_are_revalidated_and_unsafe_ones_never_submitted() -> None:
    pages = [mp("/ok", PageType.PRODUCT, 0.9), mp("/sneaky", PageType.PRICING, 0.95)]
    provider = FakeCrawlProvider()

    # 'acme.com' now resolves to a private address: nothing may be submitted.
    result = await crawler(provider, dns={"acme.com": ["192.168.1.5"]}).crawl(
        "acme.com", pages, max_pages=5
    )

    assert provider.batch_calls == [] and provider.scrape_calls == []
    assert result.skipped_pages == 2 and result.crawl_status == CrawlStatus.EMPTY
    assert all(
        "url_rejected:blocked_ip" in (r.error or "") for r in result.page_results
    )


async def test_domain_restriction_is_enforced_at_crawl_time() -> None:
    provider = FakeCrawlProvider()
    pages = [mp("/ok", PageType.PRODUCT, 0.9)]

    result = await crawler(provider).crawl(
        "acme.com", pages, allowed_domains=["other.org"]
    )

    assert provider.batch_calls == [] and result.skipped_pages == 1
    assert "domain_not_allowed" in result.page_results[0].error


async def test_robots_txt_blocks_disallowed_pages() -> None:
    async def fetcher(url: str) -> str | None:
        return "User-agent: *\nDisallow: /pricing\n"

    provider = FakeCrawlProvider()
    robots = RobotsChecker(make_policy(), fetcher)

    result = await crawler(provider, robots=robots).crawl(
        "acme.com", MAPPED, max_pages=3
    )

    assert f"{SITE}/pricing" not in provider.batch_calls[0]
    assert any(a.action == "robots_blocked" for a in result.audit)
    blocked = next(r for r in result.page_results if r.status == "skipped")
    assert blocked.error == "blocked_by_robots_txt"


async def test_missing_robots_txt_allows_crawling() -> None:
    async def fetcher(url: str) -> str | None:
        return None

    robots = RobotsChecker(make_policy(), fetcher)

    assert await robots.allowed(f"{SITE}/anything") is True


async def test_no_eligible_pages_is_an_empty_crawl() -> None:
    provider = FakeCrawlProvider()

    result = await crawler(provider).crawl(
        "acme.com", [mp("/login", PageType.UNKNOWN, 0, eligible=False)]
    )

    assert result.crawl_status == CrawlStatus.EMPTY and provider.batch_calls == []
