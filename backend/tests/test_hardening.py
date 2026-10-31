"""Hardening tests: global limits, hard deadlines, budgets, cancellation, breakers."""

import asyncio
import time
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core import metrics
from app.core.config import Settings
from app.core.exceptions import PersistenceException
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.research.source_manager import SourceManager
from app.research.website_discovery import WebsiteDiscovery
from app.schemas.intelligence import DiscoverWebsitesRequest
from app.tools.errors import (
    ProviderBudgetError,
    ProviderCircuitOpenError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.tools.firecrawl_tool import (
    FIRECRAWL_BATCH_URL,
    FIRECRAWL_SCRAPE_URL,
    FirecrawlTool,
)
from app.tools.http import CircuitBreaker
from app.tools.serpapi_tool import SERPAPI_URL, SerpApiTool
from tests.fakes import make_policy
from tests.intel_support import QuerySearch, result

OK = {"success": True, "data": {"markdown": "# T\n\nBody text here"}}


def settings(base: Settings, **update) -> Settings:
    return base.model_copy(update=update)


# -- global concurrency -----------------------------------------------------------


@respx.mock
async def test_concurrent_jobs_share_one_firecrawl_limit(keyed_settings, fake_sleep):
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=OK)

    respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=slow)
    cfg = settings(keyed_settings, firecrawl_max_concurrent_requests=2)
    jobs = [FirecrawlTool(cfg, sleep=fake_sleep) for _ in range(3)]  # 3 API jobs

    await asyncio.gather(
        *(
            job.scrape_many([f"https://example.com/{n}/{i}" for i in range(4)])
            for n, job in enumerate(jobs)
        )
    )

    assert peak == 2


@respx.mock
async def test_concurrent_jobs_share_one_serpapi_limit(keyed_settings, fake_sleep):
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"organic_results": []})

    respx.get(SERPAPI_URL).mock(side_effect=slow)
    cfg = settings(keyed_settings, serpapi_max_concurrent_requests=1)

    await asyncio.gather(
        *(SerpApiTool(cfg, sleep=fake_sleep).search(f"q{i}") for i in range(5))
    )

    assert peak == 1


# -- hard deadlines, timeouts, cancellation ---------------------------------------


@respx.mock
async def test_batch_deadline_cancels_an_in_flight_poll(keyed_settings, fake_sleep):
    respx.post(FIRECRAWL_BATCH_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "id": "j1"})
    )

    async def hang(request):
        await asyncio.sleep(30)  # a poll that would far exceed the deadline
        return httpx.Response(200, json={"status": "scraping"})

    respx.get(f"{FIRECRAWL_BATCH_URL}/j1").mock(side_effect=hang)
    cfg = settings(keyed_settings, website_research_batch_timeout_seconds=0.2)

    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError, match="did not finish"):
        await FirecrawlTool(cfg, sleep=fake_sleep).batch_scrape(
            ["https://example.com/a"]
        )

    assert time.monotonic() - started < 3


@respx.mock
async def test_timeouts_have_their_own_error_category(keyed_settings, fake_sleep):
    respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    cfg = settings(keyed_settings, research_max_retries=0)

    with pytest.raises(ProviderTimeoutError) as caught:
        await FirecrawlTool(cfg, sleep=fake_sleep).scrape_url("https://example.com/")

    assert caught.value.code == "provider_timeout"
    assert isinstance(caught.value, ProviderUnavailableError)  # old handlers still work


@respx.mock
async def test_cancellation_stops_the_request_and_releases_the_slot(
    keyed_settings, fake_sleep
):
    seen: list[str] = []

    async def hang(request):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return httpx.Response(200, json=OK)

    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=hang)
    cfg = settings(keyed_settings, firecrawl_max_concurrent_requests=1)
    task = asyncio.create_task(
        FirecrawlTool(cfg, sleep=fake_sleep).scrape_url("https://example.com/a")
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == ["cancelled"]
    route.mock(return_value=httpx.Response(200, json=OK))
    page = await asyncio.wait_for(
        FirecrawlTool(cfg, sleep=fake_sleep).scrape_url("https://example.com/b"), 2
    )
    assert page.markdown  # the single slot was released


# -- retry budget ------------------------------------------------------------------


@respx.mock
async def test_run_budget_caps_requests_including_retries(keyed_settings, fake_sleep):
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(503))
    cfg = settings(keyed_settings, research_max_retries=6)
    run = metrics.start_run(request_budget=3)

    with pytest.raises(ProviderBudgetError):
        await FirecrawlTool(cfg, sleep=fake_sleep).scrape_url("https://example.com/")

    assert route.call_count == 3
    assert run.counts["requests.firecrawl"] == 3
    assert run.counts["requests_blocked_by_budget"] >= 1


@respx.mock
async def test_budget_error_is_not_retryable(keyed_settings, fake_sleep):
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(200, json=OK))
    metrics.start_run(request_budget=1)
    tool = FirecrawlTool(keyed_settings, sleep=fake_sleep)
    await tool.scrape_url("https://example.com/a")

    with pytest.raises(ProviderBudgetError) as caught:
        await tool.scrape_url("https://example.com/b")

    assert not caught.value.retryable
    assert caught.value.code == "provider_budget_exceeded"


# -- circuit breaker ----------------------------------------------------------------


def test_circuit_breaker_opens_probes_and_closes() -> None:
    now = [0.0]
    breaker = CircuitBreaker(2, 10.0, clock=lambda: now[0])
    assert breaker.allow()
    breaker.failure()
    assert breaker.allow()
    breaker.failure()  # threshold reached
    assert not breaker.allow()
    now[0] = 11
    assert breaker.allow()  # one half-open probe
    assert not breaker.allow()  # others wait for its verdict
    breaker.failure()  # probe failed: open again
    assert not breaker.allow()
    now[0] = 25
    assert breaker.allow()
    breaker.success()
    assert breaker.allow() and breaker.failures == 0


@respx.mock
async def test_repeated_outages_pause_the_provider(keyed_settings, fake_sleep):
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(503))
    cfg = settings(
        keyed_settings, research_max_retries=0, provider_circuit_failure_threshold=2
    )
    tool = FirecrawlTool(cfg, sleep=fake_sleep)
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await tool.scrape_url("https://example.com/")

    with pytest.raises(ProviderCircuitOpenError) as caught:
        await tool.scrape_url("https://example.com/")

    assert route.call_count == 2  # the third call never left the process
    assert not caught.value.retryable


@respx.mock
async def test_authentication_errors_do_not_open_the_circuit(
    keyed_settings, fake_sleep
):
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(401))
    cfg = settings(
        keyed_settings, research_max_retries=0, provider_circuit_failure_threshold=1
    )
    tool = FirecrawlTool(cfg, sleep=fake_sleep)
    for _ in range(3):
        with pytest.raises(Exception, match="credentials"):
            await tool.scrape_url("https://example.com/")

    assert route.call_count == 3


# -- oversized responses ------------------------------------------------------------


@respx.mock
async def test_oversized_serpapi_response_is_rejected(keyed_settings, fake_sleep):
    big = b"{" + b" " * (6 * 1024 * 1024) + b"}"
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, content=big))

    with pytest.raises(ProviderResponseError, match="size limit"):
        await SerpApiTool(keyed_settings, sleep=fake_sleep).search("q")


@respx.mock
async def test_oversized_firecrawl_response_is_rejected(keyed_settings, fake_sleep):
    big = b"{" + b" " * (9 * 1024 * 1024) + b"}"
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(200, content=big))
    cfg = settings(keyed_settings, research_max_retries=0)

    with pytest.raises(ProviderResponseError, match="size limit"):
        await FirecrawlTool(cfg, sleep=fake_sleep).scrape_url("https://example.com/")


# -- configuration / persistence / discovery ----------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"research_retry_base_delay_seconds": 5, "research_retry_max_delay_seconds": 1},
        {
            "website_research_batch_poll_seconds": 30,
            "website_research_batch_timeout_seconds": 5,
        },
    ],
)
def test_contradictory_settings_are_rejected(bad) -> None:
    with pytest.raises(ValidationError, match="inconsistent settings"):
        Settings(**bad)


def test_defaults_are_consistent() -> None:
    cfg = Settings()
    assert cfg.website_research_map_timeout_seconds <= 300
    assert cfg.firecrawl_max_concurrent_requests == 2


class BrokenSession:
    """A session whose database connection is gone."""

    def add(self, *_a, **_k) -> None: ...

    async def execute(self, *_a, **_k):
        raise OperationalError("SELECT 1", {}, Exception("password=hunter2 refused"))

    flush = commit = get = execute

    async def rollback(self) -> None: ...


async def test_database_connection_loss_becomes_a_safe_persistence_error() -> None:
    repo = IntelligenceRepository(BrokenSession())  # type: ignore[arg-type]

    with pytest.raises(PersistenceException) as caught:
        await repo.create_job(uuid4(), "Acme", 5, None, None)

    assert "hunter2" not in str(caught.value)
    assert "SELECT" not in str(caught.value)


async def test_ambiguous_official_domains_are_flagged() -> None:
    search = QuerySearch(
        {
            "Basecamp": [
                result(1, "https://basecamp.com/", "Basecamp", "Basecamp tool"),
                result(2, "https://basecamp.org/", "Basecamp", "Basecamp charity"),
            ]
        }
    )
    discovery = WebsiteDiscovery(search, SourceManager(make_policy()), Settings())

    out = await discovery.discover(
        DiscoverWebsitesRequest(query="Basecamp", target_names=["Basecamp"])
    )

    assert any("ambiguous" in note for note in out.notes)
    assert {c.domain for c in out.candidates} == {"basecamp.com", "basecamp.org"}


@pytest.mark.parametrize(
    ("url", "unsafe"),
    [
        ("postgresql+asyncpg://u:p@localhost:5432/aurora", True),
        ("postgresql+asyncpg://u:p@localhost:5432/aurora_test", True),
        ("postgresql+asyncpg://u:p@localhost:5432/aurora_live_test", True),
        ("postgresql+asyncpg://u:p@db.example.com:5432/aurora_test_final", True),
        ("postgresql+asyncpg://u:p@localhost:5432/aurora_test_final", False),
        (None, True),
    ],
)
def test_postgres_test_target_guard(url, unsafe) -> None:
    from tests.test_research_integration import _unsafe_target

    assert (_unsafe_target(url) is not None) is unsafe
