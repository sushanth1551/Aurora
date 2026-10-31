"""Tests for caching, concurrency caps, batch deadlines and run metrics (mocked)."""

import asyncio

import httpx
import pytest
import respx

from app.core import metrics
from app.research.cache import CachedMapper, CachedSearch, TTLCache
from app.research.website_discovery import is_search_engine_wrapper
from app.schemas.intelligence import IntelligenceRequest
from app.schemas.research import SearchResult
from app.tools.errors import ProviderUnavailableError
from app.tools.firecrawl_tool import (
    FIRECRAWL_BATCH_URL,
    FIRECRAWL_SCRAPE_URL,
    FirecrawlTool,
    MapLink,
)
from tests.intel_support import (
    FakeCrawlProvider,
    FakeMapTool,
    acme_links,
    acme_pages,
    acme_search,
    build_service,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_expires_evicts_and_can_be_disabled() -> None:
    clock = Clock()
    cache: TTLCache[int] = TTLCache(10, max_entries=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # evicts the least recently used ("a")
    assert cache.get("a") is None and cache.get("c") == 3
    clock.now = 11
    assert cache.get("c") is None  # expired
    off: TTLCache[int] = TTLCache(0)
    off.set("x", 1)
    assert off.get("x") is None and not off.enabled


class CountingSearch:
    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    async def search(self, query, *, num_results=10, country=None, language=None):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ProviderUnavailableError("SerpApi", "down")
        return [SearchResult(position=1, title="t", url="https://acme.com/")]


async def test_repeated_search_is_served_from_cache_and_failures_are_not_cached():
    inner = CountingSearch(fail_first=True)
    cached = CachedSearch(inner, TTLCache(60))
    with pytest.raises(ProviderUnavailableError):
        await cached.search("acme")
    first = await cached.search("acme")
    first[0].title = "mutated"  # callers cannot corrupt the cache
    second = await cached.search("acme")
    other = await cached.search("acme", country="in")

    assert inner.calls == 3  # failure, one miss, one different-key miss
    assert second[0].title == "t" and other


async def test_repeated_map_is_served_from_cache_and_counted() -> None:
    class Mapper:
        calls = 0

        async def map_site(self, url, **kwargs):
            self.calls += 1
            return [MapLink(url="https://acme.com/pricing")]

    inner = Mapper()
    cached = CachedMapper(inner, TTLCache(60))
    run = metrics.start_run()
    await cached.map_site("https://acme.com/", limit=50)
    await cached.map_site("https://acme.com/", limit=50)
    await cached.map_site("https://acme.com/", limit=10)  # different limit

    assert inner.calls == 2 and run.counts["cache_hit.map"] == 1


@respx.mock
async def test_firecrawl_simultaneous_requests_are_capped(keyed_settings, fake_sleep):
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(
            200, json={"success": True, "data": {"markdown": "# T\n\nBody text"}}
        )

    respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=slow)
    settings = keyed_settings.model_copy(
        update={"firecrawl_max_concurrent_requests": 2}
    )
    tool = FirecrawlTool(settings, sleep=fake_sleep)

    urls = [f"https://example.com/{i}" for i in range(8)]
    pages = await tool.scrape_many(urls, concurrency=8)  # caller asks for more

    assert len(pages) == 8 and peak == 2


@respx.mock
async def test_batch_polling_is_bounded_by_poll_count(keyed_settings, fake_sleep):
    settings = keyed_settings.model_copy(update={"website_research_batch_max_polls": 3})
    respx.post(FIRECRAWL_BATCH_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "id": "job1"})
    )
    status = respx.get(f"{FIRECRAWL_BATCH_URL}/job1").mock(
        return_value=httpx.Response(200, json={"status": "scraping"})
    )

    with pytest.raises(ProviderUnavailableError, match="did not finish"):
        await FirecrawlTool(settings, sleep=fake_sleep).batch_scrape(
            ["https://example.com/a", "https://example.com/b"]
        )

    assert status.call_count == 3


@respx.mock
async def test_batch_polling_is_bounded_by_total_duration(keyed_settings, fake_sleep):
    settings = keyed_settings.model_copy(
        update={"website_research_batch_timeout_seconds": 0.001}
    )
    respx.post(FIRECRAWL_BATCH_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "id": "job1"})
    )
    status = respx.get(f"{FIRECRAWL_BATCH_URL}/job1").mock(
        return_value=httpx.Response(200, json={"status": "scraping"})
    )

    async def slow_sleep(delay: float) -> None:
        await asyncio.sleep(0.01)

    with pytest.raises(ProviderUnavailableError, match="did not finish"):
        await FirecrawlTool(settings, sleep=slow_sleep).batch_scrape(
            ["https://example.com/a"]
        )

    assert status.call_count <= 2


@respx.mock
async def test_batch_start_timeout_is_not_retried(keyed_settings, fake_sleep):
    start = respx.post(FIRECRAWL_BATCH_URL).mock(side_effect=httpx.ReadTimeout("x"))

    with pytest.raises(ProviderUnavailableError):
        await FirecrawlTool(keyed_settings, sleep=fake_sleep).batch_scrape(
            ["https://example.com/a"]
        )

    assert start.call_count == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.google.com/url?q=https://basecamp.com/", True),
        ("https://www.google.com/search?q=basecamp", True),
        ("https://www.google.com/aclk?sa=l", True),
        ("https://www.bing.com/ck/a?!&&p=abc", True),
        ("https://basecamp.com/url", False),
        ("https://www.google.com/about", False),
    ],
)
def test_search_engine_wrappers_are_detected(url: str, expected: bool) -> None:
    assert is_search_engine_wrapper(url) is expected


async def test_run_reports_metrics_without_sensitive_data(db_session) -> None:
    service, _ = build_service(
        db_session,
        search=acme_search(),
        mapper=FakeMapTool(acme_links()),
        crawl=FakeCrawlProvider(acme_pages()),
    )

    response = await service.run(
        IntelligenceRequest(query="Acme", target_names=["Acme"], max_websites=1)
    )

    data = response.metrics
    assert set(data) == {"durations_ms", "counts", "provider_requests"}
    for key in ("discovery", "mapping", "crawling", "persistence", "total"):
        assert key in data["durations_ms"]
    assert data["counts"]["urls_discovered"] >= 1
    assert data["counts"]["pages_scraped"] == response.crawled_pages
    assert "acme.com" not in str(data) and "http" not in str(data)
