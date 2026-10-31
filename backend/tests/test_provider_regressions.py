"""Regression tests for issues found in the first live-provider run.

1. SerpApi ``source`` is a publisher name, not a domain; ``domain`` must come
   from the destination URL, and Google-owned pages are never a company site.
2. Injected HTTP clients ignored the adapters' configured timeouts (HTTPX's
   5-second default applied), and ``/v1/map`` needs a longer read timeout.
All HTTP is mocked; no provider is contacted.
"""

import httpx
import pytest
import respx

from app.core.config import Settings
from app.research.source_manager import SourceManager
from app.research.website_discovery import WebsiteDiscovery
from app.schemas.intelligence import (
    DiscoverWebsitesRequest,
    OfficialStatus,
    WebsiteType,
)
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.tools.firecrawl_tool import (
    FIRECRAWL_MAP_URL,
    FIRECRAWL_SCRAPE_URL,
    FirecrawlTool,
)
from app.tools.serpapi_tool import SERPAPI_URL, SerpApiTool
from tests.fakes import make_policy
from tests.intel_support import QuerySearch, result

# -- SerpApi parsing -------------------------------------------------------------

MISLEADING = {
    "search_metadata": {"id": "s1"},
    "ads": [{"title": "Ad", "link": "https://ads.example.net/x"}],
    "knowledge_graph": {"title": "KG", "website": "https://kg.example.net/"},
    "answer_box": {"link": "https://answer.example.net/"},
    "top_stories": [{"title": "News", "link": "https://news.example.net/s"}],
    "videos": [{"title": "Video", "link": "https://www.youtube.com/watch?v=1"}],
    "organic_results": [
        {
            "position": 1,
            "title": "Basecamp on Apple",
            "link": "https://www.apple.com/newsroom/basecamp",
            "source": "Apple",
            "displayed_link": "https://www.apple.com › newsroom",
        },
        {
            "position": 2,
            "title": "Tutorial",
            "link": "https://www.youtube.com/watch?v=abc",
            "source": "YouTube · Speak About Digital",
        },
        {
            "position": 3,
            "title": "Basecamp - Apps on Google Play",
            "link": "https://play.google.com/store/apps/details?id=x",
            "source": "Google Play",
        },
    ],
}


@respx.mock
async def test_domain_comes_from_destination_url_not_publisher(
    keyed_settings, fake_sleep
) -> None:
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=MISLEADING))

    found = await SerpApiTool(keyed_settings, sleep=fake_sleep).search("basecamp")

    assert [r.domain for r in found] == [
        "www.apple.com",
        "www.youtube.com",
        "play.google.com",
    ]
    assert found[0].metadata["publisher"] == "Apple"
    assert found[1].metadata["publisher"] == "YouTube · Speak About Digital"


@respx.mock
async def test_only_organic_results_are_returned(keyed_settings, fake_sleep) -> None:
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=MISLEADING))

    found = await SerpApiTool(keyed_settings, sleep=fake_sleep).search("basecamp")

    urls = " ".join(r.url for r in found)
    for excluded in ("ads.example.net", "kg.example.net", "answer.example.net"):
        assert excluded not in urls
    assert "news.example.net" not in urls and "watch?v=1" not in urls


# -- discovery with misleading results -------------------------------------------


def discovery(search) -> WebsiteDiscovery:
    return WebsiteDiscovery(search, SourceManager(make_policy()), Settings())


def request(**kwargs) -> DiscoverWebsitesRequest:
    return DiscoverWebsitesRequest(**{"query": "Basecamp", **kwargs})


async def test_google_properties_are_never_company_candidates() -> None:
    search = QuerySearch(
        {
            "Basecamp": [
                result(
                    1, "https://play.google.com/store/apps/details?id=x", "Basecamp"
                ),
                result(2, "https://www.google.com/search?q=basecamp", "Basecamp"),
                result(3, "https://support.google.com/a/answer/1", "Basecamp help"),
                result(4, "https://apps.apple.com/us/app/basecamp/id1", "Basecamp"),
            ]
        }
    )

    outcome = await discovery(search).discover(request(target_names=["Basecamp"]))

    assert outcome.candidates == []
    assert any("third-party" in n for n in outcome.notes)
    assert len(outcome.search_results) >= 4  # raw results are still preserved


async def test_publisher_style_names_do_not_make_a_site_official() -> None:
    first = result(1, "https://www.apple.com/newsroom/x", "Basecamp story", "Basecamp")
    first.domain = "Apple"  # what the old parser stored
    search = QuerySearch(
        {
            "Basecamp": [
                first,
                result(2, "https://basecamp.com/", "Basecamp", "Project management"),
            ]
        }
    )

    outcome = await discovery(search).discover(request(target_names=["Basecamp"]))
    by_domain = {c.domain: c for c in outcome.candidates}

    assert by_domain["basecamp.com"].official_status == OfficialStatus.VERIFIED
    assert by_domain["apple.com"].official_status == OfficialStatus.UNVERIFIED
    assert outcome.candidates[0].domain == "basecamp.com"


async def test_google_is_official_only_when_google_is_the_subject() -> None:
    search = QuerySearch(
        {"Google": [result(1, "https://www.google.com/", "Google", "Search")]}
    )

    outcome = await discovery(search).discover(
        request(query="Google", target_names=["Google"])
    )
    assert outcome.candidates[0].official_status == OfficialStatus.VERIFIED
    assert outcome.candidates[0].website_type == WebsiteType.OFFICIAL

    play = QuerySearch(
        {"Google": [result(1, "https://play.google.com/store", "Google", "Google")]}
    )
    again = await discovery(play).discover(
        request(query="Google", target_names=["Google"])
    )
    assert again.candidates == []  # a subdomain property is not "Google" itself


# -- timeouts ----------------------------------------------------------------------


def slow_client() -> httpx.AsyncClient:
    """A client carrying HTTPX's own tiny default, like the live harness did."""
    return httpx.AsyncClient(timeout=httpx.Timeout(1.0))


@respx.mock
async def test_scrape_timeout_comes_from_settings_not_client_default(
    keyed_settings, fake_sleep
) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"markdown": "# Hi\n\nBody"}}
        )
    )
    async with slow_client() as client:
        await FirecrawlTool(keyed_settings, client=client, sleep=fake_sleep).scrape_url(
            "https://example.com/"
        )

    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == keyed_settings.research_http_read_timeout_seconds
    assert timeout["connect"] == keyed_settings.research_http_connect_timeout_seconds


@respx.mock
async def test_map_uses_longer_dedicated_read_timeout(
    keyed_settings, fake_sleep
) -> None:
    settings = keyed_settings.model_copy(
        update={"website_research_map_timeout_seconds": 77.0}
    )
    route = respx.post(FIRECRAWL_MAP_URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "links": ["https://example.com/a"]}
        )
    )
    async with slow_client() as client:
        links = await FirecrawlTool(settings, client=client, sleep=fake_sleep).map_site(
            "https://example.com/"
        )

    assert [link.url for link in links] == ["https://example.com/a"]
    assert route.calls.last.request.extensions["timeout"]["read"] == 77.0
    assert 77.0 > settings.research_http_read_timeout_seconds


@respx.mock
async def test_serpapi_timeout_comes_from_settings(keyed_settings, fake_sleep) -> None:
    route = respx.get(SERPAPI_URL).mock(
        return_value=httpx.Response(200, json={"organic_results": []})
    )
    async with slow_client() as client:
        await SerpApiTool(keyed_settings, client=client, sleep=fake_sleep).search("x")

    read = route.calls.last.request.extensions["timeout"]["read"]
    assert read == keyed_settings.research_http_read_timeout_seconds


def no_retry(settings: Settings) -> Settings:
    return settings.model_copy(update={"research_max_retries": 0})


@respx.mock
async def test_map_timeout_is_reported_without_retry_when_retries_are_zero(
    keyed_settings, fake_sleep
) -> None:
    route = respx.post(FIRECRAWL_MAP_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(ProviderUnavailableError, match="timed out"):
        await FirecrawlTool(no_retry(keyed_settings), sleep=fake_sleep).map_site(
            "https://example.com/"
        )

    assert route.call_count == 1


@respx.mock
async def test_map_timeout_is_never_retried(keyed_settings, fake_sleep, sleeps) -> None:
    """A timed-out map may already have been billed, so it is reported, not repeated."""
    route = respx.post(FIRECRAWL_MAP_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(ProviderUnavailableError):
        await FirecrawlTool(keyed_settings, sleep=fake_sleep).map_site(
            "https://example.com/"
        )

    assert route.call_count == 1 and sleeps == []


@respx.mock
async def test_map_server_error_is_not_retried(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_MAP_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ProviderUnavailableError):
        await FirecrawlTool(keyed_settings, sleep=fake_sleep).map_site(
            "https://example.com/"
        )

    assert route.call_count == 1


@respx.mock
async def test_map_rate_limit_honours_retry_after_within_budget(
    keyed_settings, fake_sleep, sleeps
) -> None:
    limited = httpx.Response(429, headers={"Retry-After": "7"})
    ok = httpx.Response(200, json={"success": True, "links": []})
    route = respx.post(FIRECRAWL_MAP_URL).mock(side_effect=[limited, ok])

    await FirecrawlTool(keyed_settings, sleep=fake_sleep).map_site(
        "https://example.com/"
    )

    assert route.call_count == 2  # exactly one retry (WEBSITE_RESEARCH_MAX_RETRIES=1)
    assert sleeps and sleeps[0] >= 7


@respx.mock
async def test_map_rate_limit_retries_are_bounded(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_MAP_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )

    with pytest.raises(ProviderRateLimitError):
        await FirecrawlTool(keyed_settings, sleep=fake_sleep).map_site(
            "https://example.com/"
        )

    assert route.call_count == keyed_settings.website_research_max_retries + 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"error": "bad key"}), ProviderAuthenticationError),
        (httpx.Response(429, headers={"Retry-After": "1"}), ProviderRateLimitError),
        (httpx.Response(503), ProviderUnavailableError),
        (httpx.Response(200, json={"success": False}), ProviderResponseError),
        (
            httpx.Response(200, json={"success": True, "links": "no"}),
            ProviderResponseError,
        ),
        (httpx.Response(200, text="<html>"), ProviderResponseError),
    ],
)
@respx.mock
async def test_map_provider_errors(response, error, fake_sleep, keyed_settings) -> None:
    respx.post(FIRECRAWL_MAP_URL).mock(return_value=response)

    with pytest.raises(error):
        await FirecrawlTool(no_retry(keyed_settings), sleep=fake_sleep).map_site(
            "https://example.com/"
        )


@respx.mock
async def test_map_accepts_link_objects_and_respects_limit(
    keyed_settings, fake_sleep
) -> None:
    payload = {
        "success": True,
        "links": [
            {"url": "https://example.com/a", "title": "A"},
            "https://example.com/b",
            {"title": "no url"},
            "https://example.com/c",
        ],
    }
    respx.post(FIRECRAWL_MAP_URL).mock(return_value=httpx.Response(200, json=payload))

    links = await FirecrawlTool(keyed_settings, sleep=fake_sleep).map_site(
        "https://example.com/", limit=2
    )

    assert [link.url for link in links] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


# -- real SerpApi shape: relative Google redirect in ``link`` -------------------------


def _goto(position, title, displayed, source, snippet=""):
    return {
        "position": position,
        "title": title,
        "link": f"/goto?url=TOKEN{position}",
        "redirect_link": f"https://www.google.com/url?sa=t&url=TOKEN{position}",
        "displayed_link": displayed,
        "snippet": snippet,
        "source": source,
    }


GOTO_PAYLOAD = {
    "search_metadata": {"id": "real-shape"},
    "ai_overview": {"text": "ignored"},
    "organic_results": [
        _goto(1, "Basecamp", "https://basecamp.com", "Basecamp", "Basecamp is a tool"),
        _goto(
            2,
            "The Spot to Explore",
            "https://www.basecamphotels.com › tahoe",
            "Basecamp Hotels",
            "Stay",
        ),
        _goto(
            3,
            "Garmin BaseCamp Overview",
            "https://support.garmin.com › en-US",
            "Garmin",
            "BaseCamp maps",
        ),
        _goto(4, "Elsewhere", "no scheme here", "Nobody"),
    ],
}


@respx.mock
async def test_relative_google_redirect_links_use_the_displayed_host(
    keyed_settings, fake_sleep
) -> None:
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=GOTO_PAYLOAD))

    found = await SerpApiTool(keyed_settings, sleep=fake_sleep).search("basecamp")

    assert [r.url for r in found] == [
        "https://basecamp.com/",
        "https://www.basecamphotels.com/",
        "https://support.garmin.com/",
    ]
    assert [r.domain for r in found] == [
        "basecamp.com",
        "www.basecamphotels.com",
        "support.garmin.com",
    ]
    assert all(r.metadata["destination_source"] == "displayed_link" for r in found)
    assert all("google.com" not in r.url and "/goto" not in r.url for r in found)


@respx.mock
async def test_absolute_google_redirects_never_become_destinations(
    keyed_settings, fake_sleep
) -> None:
    payload = {
        "organic_results": [
            {
                "position": 1,
                "title": "Acme",
                "link": "https://www.google.com/url?q=https://acme.com/",
                "displayed_link": "https://acme.com",
            }
        ]
    }
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=payload))

    found = await SerpApiTool(keyed_settings, sleep=fake_sleep).search("acme")

    assert [r.url for r in found] == ["https://acme.com/"]


@respx.mock
async def test_real_shape_discovery_prefers_the_matching_domain(
    keyed_settings, fake_sleep
) -> None:
    respx.get(SERPAPI_URL).mock(return_value=httpx.Response(200, json=GOTO_PAYLOAD))
    tool = SerpApiTool(keyed_settings, sleep=fake_sleep)

    out = await discovery(tool).discover(request(target_names=["Basecamp"]))

    top = out.candidates[0]
    assert top.domain == "basecamp.com"
    assert top.official_status == OfficialStatus.VERIFIED
    others = {c.domain: c.official_status for c in out.candidates[1:]}
    assert OfficialStatus.VERIFIED not in others.values()
    assert "google.com" not in {c.domain for c in out.candidates}
