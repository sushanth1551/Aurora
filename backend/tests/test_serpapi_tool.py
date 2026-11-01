"""Tests for the SerpApi adapter using mocked HTTP (no real credits used)."""

import logging

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.tools.serpapi_tool import SERPAPI_URL, SerpApiTool

PAYLOAD = {
    "search_metadata": {"id": "abc123", "status": "Success"},
    "organic_results": [
        {
            "position": 1,
            "title": "First",
            "link": "https://example.com/a",
            "snippet": "Snippet A",
            "displayed_link": "example.com › a",
            "source": "Example",
        },
        {"link": "https://example.org/b"},  # missing title/snippet/position
        {"title": "No link"},  # skipped: no link
        "garbage",  # skipped: not an object
    ],
}


def tool(settings, fake_sleep) -> SerpApiTool:
    return SerpApiTool(settings, sleep=fake_sleep)


@respx.mock
async def test_search_normalizes_results(keyed_settings, fake_sleep) -> None:
    route = respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))

    results = await tool(keyed_settings, fake_sleep).search(
        "ai tools", num_results=5, country="in", language="en"
    )

    assert [r.url for r in results] == [
        "https://example.com/a",
        "https://example.org/b",
    ]
    first, second = results
    assert first.title == "First" and first.snippet == "Snippet A"
    assert first.position == 1 and second.position == 2  # fallback to index
    assert second.title == "" and second.snippet is None
    assert first.metadata["search_id"] == "abc123"
    params = dict(route.calls.last.request.url.params)
    assert params["q"] == "ai tools" and params["gl"] == "in" and params["hl"] == "en"
    assert params["num"] == "5" and params["engine"] == "google"


@respx.mock
async def test_num_results_is_clamped_and_limits_output(
    keyed_settings, fake_sleep
) -> None:
    organic = [{"link": f"https://example.com/{i}"} for i in range(5)]
    route = respx.get(SERPAPI_URL).mock(
        return_value=httpx.Response(200, json={"organic_results": organic})
    )

    results = await tool(keyed_settings, fake_sleep).search("q", num_results=3)
    await tool(keyed_settings, fake_sleep).search("q", num_results=9999)

    assert len(results) == 3
    assert route.calls.last.request.url.params["num"] == "100"


@respx.mock
async def test_no_results_returns_empty_list(keyed_settings, fake_sleep) -> None:
    respx.get(SERPAPI_URL).mock(
        return_value=httpx.Response(
            200, json={"error": "Google hasn't returned any results for this query."}
        )
    )
    assert await tool(keyed_settings, fake_sleep).search("zzzz") == []


@respx.mock
async def test_missing_organic_results_is_empty(keyed_settings, fake_sleep) -> None:
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json={}))
    assert await tool(keyed_settings, fake_sleep).search("q") == []


@respx.mock
async def test_authentication_failure_is_not_retried(
    keyed_settings, fake_sleep, sleeps
) -> None:
    route = respx.get(SERPAPI_URL).mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key"})
    )

    with pytest.raises(ProviderAuthenticationError):
        await tool(keyed_settings, fake_sleep).search("q")

    assert route.call_count == 1 and sleeps == []


@respx.mock
async def test_bad_request_is_not_retried(keyed_settings, fake_sleep) -> None:
    route = respx.get(SERPAPI_URL).mock(return_value=httpx.Response(400, json={}))

    with pytest.raises(ProviderRequestError):
        await tool(keyed_settings, fake_sleep).search("q")

    assert route.call_count == 1


@respx.mock
async def test_rate_limit_honours_retry_after_then_succeeds(
    keyed_settings, fake_sleep, sleeps
) -> None:
    route = respx.get(SERPAPI_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=PAYLOAD),
        ]
    )

    results = await tool(keyed_settings, fake_sleep).search("q")

    assert len(results) == 2 and route.call_count == 2
    assert sleeps == [pytest.approx(7.0)]  # never shorter than Retry-After


@respx.mock
async def test_rate_limit_exhausts_bounded_retries(
    keyed_settings, fake_sleep, sleeps
) -> None:
    route = respx.get(SERPAPI_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(ProviderRateLimitError):
        await tool(keyed_settings, fake_sleep).search("q")

    assert route.call_count == keyed_settings.research_max_retries + 1
    assert len(sleeps) == keyed_settings.research_max_retries


@respx.mock
async def test_transient_failures_back_off_exponentially(
    keyed_settings, fake_sleep, sleeps
) -> None:
    respx.get(SERPAPI_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(200, json=PAYLOAD),
        ]
    )

    assert len(await tool(keyed_settings, fake_sleep).search("q")) == 2
    assert len(sleeps) == 2
    assert 0 <= sleeps[0] <= 0.5 and 0 <= sleeps[1] <= 1.0  # capped, jittered


@respx.mock
async def test_timeout_is_retried_then_raised(keyed_settings, fake_sleep) -> None:
    route = respx.get(SERPAPI_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(ProviderUnavailableError):
        await tool(keyed_settings, fake_sleep).search("q")

    assert route.call_count == keyed_settings.research_max_retries + 1


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["list"]),
        httpx.Response(200, json={"organic_results": "nope"}),
        httpx.Response(200, json={"error": "Something broke"}),
    ],
)
async def test_malformed_responses(keyed_settings, fake_sleep, response) -> None:
    respx.get(SERPAPI_URL).mock(return_value=response)

    with pytest.raises(ProviderResponseError):
        await tool(keyed_settings, fake_sleep).search("q")


async def test_missing_key_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationException) as excinfo:
        await SerpApiTool(Settings()).search("q")
    assert "SERPAPI_API_KEY" in str(excinfo.value)


@respx.mock
async def test_api_key_never_appears_in_logs_or_errors(
    keyed_settings, fake_sleep, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(401))
    secret = keyed_settings.serpapi_api_key.get_secret_value()

    with pytest.raises(ProviderAuthenticationError) as excinfo:
        await tool(keyed_settings, fake_sleep).search("q")

    assert secret not in str(excinfo.value) and secret not in caplog.text


# -- search_serp (Phase 3) -------------------------------------------------------------

SERP_PAYLOAD = {
    **PAYLOAD,
    "related_questions": [
        {"question": "What is it?", "link": "https://example.com/faq"}
    ],
    "related_searches": [{"query": "related thing"}],
    "answer_box": {
        "type": "organic_result",
        "snippet": "Direct answer",
        "link": "https://example.com/a",
    },
    "ai_overview": {"page_token": "tok"},
}


@respx.mock
async def test_search_serp_sends_device_and_location_and_parses_features(
    keyed_settings, fake_sleep
) -> None:
    route = respx.get(SERPAPI_URL).mock(
        return_value=httpx.Response(200, json=SERP_PAYLOAD)
    )
    snapshot = await tool(keyed_settings, fake_sleep).search_serp(
        "email tools",
        num_results=10,
        country="us",
        language="en",
        device="mobile",
        location="Austin, Texas",
    )
    params = dict(route.calls.last.request.url.params)
    assert (
        params["q"] == "email tools"
        and params["device"] == "mobile"
        and params["location"] == "Austin, Texas"
    )
    assert (
        params["gl"] == "us" and params["hl"] == "en" and route.call_count == 1
    )  # one search credit
    assert [r.position for r in snapshot.organic] == [1, 2]
    assert snapshot.features.people_also_ask[0].question == "What is it?"
    assert snapshot.features.related_searches == ["related thing"]
    assert (
        snapshot.features.featured_snippet
        and snapshot.features.featured_snippet.domain == "example.com"
    )
    assert (
        snapshot.features.ai_overview.status == "requires_second_request"
    )  # never fetched or invented
    assert snapshot.provider_search_id == "abc123" and snapshot.device == "mobile"


@respx.mock
async def test_search_serp_ignores_unknown_devices_and_retries_like_search(
    keyed_settings, fake_sleep
) -> None:
    route = respx.get(SERPAPI_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=PAYLOAD)]
    )
    snapshot = await tool(keyed_settings, fake_sleep).search_serp("q", device="watch")
    assert route.call_count == 2 and "device" not in dict(
        route.calls.last.request.url.params
    )
    assert (
        snapshot.features.people_also_ask == []
        and snapshot.features.ai_overview.status == "absent"
    )


async def test_search_serp_requires_configuration(fake_sleep) -> None:
    with pytest.raises(ConfigurationException):
        await tool(Settings(), fake_sleep).search_serp("q")


@respx.mock
async def test_existing_search_request_is_unchanged_by_search_serp(
    keyed_settings, fake_sleep
) -> None:
    route = respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    await tool(keyed_settings, fake_sleep).search(
        "q", num_results=5, country="us", language="en"
    )
    params = dict(route.calls.last.request.url.params)
    assert set(params) == {"engine", "q", "num", "output", "api_key", "gl", "hl"}
