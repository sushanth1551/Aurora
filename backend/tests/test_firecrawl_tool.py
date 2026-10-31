"""Tests for the Firecrawl adapter using mocked HTTP (no real credits used)."""

import asyncio
import json

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.schemas.research import ExtractionStatus
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.tools.firecrawl_tool import FIRECRAWL_SCRAPE_URL, FirecrawlTool

URL = "https://example.com/article"


def success(markdown="# Title\n\nBody", metadata=None):
    data = {"markdown": markdown}
    if metadata is not None:
        data["metadata"] = metadata
    return httpx.Response(200, json={"success": True, "data": data})


def tool(settings, fake_sleep) -> FirecrawlTool:
    return FirecrawlTool(settings, sleep=fake_sleep)


@respx.mock
async def test_successful_extraction(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(
        return_value=success(
            metadata={
                "title": "T",
                "description": "D",
                "statusCode": 200,
                "url": "https://example.com/final",
                "sourceURL": URL,
                "language": "en",
            }
        )
    )

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.SUCCEEDED
    assert page.original_url == URL and page.title == "T" and page.description == "D"
    assert page.markdown.startswith("# Title") and len(page.content_hash) == 64
    assert page.provider_status == 200
    # Reported final URL is kept as *unverified* metadata only.
    assert page.final_url is None
    assert page.metadata["provider_final_url"] == "https://example.com/final"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer fc-secret-key-456"
    body = json.loads(request.content)
    assert body["url"] == URL and body["formats"] == ["markdown"]


@respx.mock
async def test_empty_markdown_is_a_failed_page(keyed_settings, fake_sleep) -> None:
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=success(markdown="   "))

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.FAILED
    assert page.metadata["error_category"] == "empty_content"
    assert page.markdown == "" and page.content_hash is None


@respx.mock
async def test_missing_metadata_is_tolerated(keyed_settings, fake_sleep) -> None:
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=success())

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.SUCCEEDED
    assert (
        page.title is None and page.provider_status is None and page.final_url is None
    )


@respx.mock
async def test_target_page_http_error_is_a_failed_page(
    keyed_settings, fake_sleep
) -> None:
    respx.post(FIRECRAWL_SCRAPE_URL).mock(
        return_value=success(metadata={"statusCode": 404})
    )

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.FAILED and page.provider_status == 404


@respx.mock
async def test_oversized_content_is_truncated(keyed_settings, fake_sleep) -> None:
    settings = keyed_settings.model_copy(update={"research_max_content_chars": 1000})
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=success(markdown="a" * 5000))

    page = await tool(settings, fake_sleep).scrape_url(URL)

    assert len(page.markdown) == 1000
    assert (
        page.metadata["truncated"] is True and page.metadata["original_length"] == 5000
    )


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"success": False, "error": "nope"}),
        httpx.Response(200, json={"success": True, "data": "string"}),
    ],
)
async def test_invalid_responses(keyed_settings, fake_sleep, response) -> None:
    respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=response)

    with pytest.raises(ProviderResponseError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)


@respx.mock
async def test_rate_limit_retries_with_retry_after(
    keyed_settings, fake_sleep, sleeps
) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), success()]
    )

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.SUCCEEDED and route.call_count == 2
    assert sleeps == [pytest.approx(3.0)]


@respx.mock
async def test_rate_limit_is_bounded(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(ProviderRateLimitError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert route.call_count == keyed_settings.research_max_retries + 1


@respx.mock
@pytest.mark.parametrize("status", [401, 402, 403])
async def test_authentication_failure_is_not_retried(
    keyed_settings, fake_sleep, status
) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(ProviderAuthenticationError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert route.call_count == 1


@respx.mock
async def test_timeout_is_retried_then_raised(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=httpx.ConnectTimeout("t"))

    with pytest.raises(ProviderUnavailableError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert route.call_count == keyed_settings.research_max_retries + 1


@respx.mock
async def test_temporary_failure_recovers(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(503), success()]
    )

    page = await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert page.status == ExtractionStatus.SUCCEEDED and route.call_count == 3


@respx.mock
async def test_bad_request_is_not_retried(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(400))

    with pytest.raises(ProviderRequestError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)

    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost/x", "file:///etc/passwd", "ftp://x.com/"],
)
async def test_unsafe_urls_are_refused_before_any_request(
    keyed_settings, fake_sleep, url
) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=success())

    with pytest.raises(ProviderRequestError):
        await tool(keyed_settings, fake_sleep).scrape_url(url)

    assert route.call_count == 0


async def test_missing_key_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationException):
        await FirecrawlTool(Settings()).scrape_url(URL)


@respx.mock
async def test_oversized_provider_response_is_rejected(
    keyed_settings, fake_sleep
) -> None:
    respx.post(FIRECRAWL_SCRAPE_URL).mock(
        return_value=httpx.Response(200, content=b"x" * (9 * 1024 * 1024))
    )

    with pytest.raises(ProviderResponseError):
        await tool(keyed_settings, fake_sleep).scrape_url(URL)


@respx.mock
async def test_scrape_many_isolates_failures_and_bounds_concurrency(
    keyed_settings, fake_sleep
) -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        if b"bad" in request.content:
            return httpx.Response(401)
        return success()

    respx.post(FIRECRAWL_SCRAPE_URL).mock(side_effect=handler)
    urls = [f"https://example.com/p{i}" for i in range(6)] + ["https://example.com/bad"]

    pages = await tool(keyed_settings, fake_sleep).scrape_many(urls, concurrency=2)

    assert [p.original_url for p in pages] == urls
    assert pages[-1].status == ExtractionStatus.FAILED
    assert pages[-1].metadata["error_category"] == "provider_authentication_error"
    assert all(p.status == ExtractionStatus.SUCCEEDED for p in pages[:-1])
    assert peak <= 2
