"""Tests for website discovery and official-site verification."""

import pytest

from app.core.config import Settings
from app.research.source_manager import SourceManager
from app.research.website_discovery import WebsiteDiscovery, build_queries
from app.schemas.intelligence import (
    DiscoverWebsitesRequest,
    OfficialStatus,
    SubjectType,
    WebsiteType,
)
from app.tools.errors import ProviderAuthenticationError
from tests.fakes import make_policy
from tests.intel_support import QuerySearch, acme_search, result


def discovery(search, dns=None, **settings) -> WebsiteDiscovery:
    return WebsiteDiscovery(
        search, SourceManager(make_policy(dns)), Settings(**settings)
    )


def request(**kwargs) -> DiscoverWebsitesRequest:
    return DiscoverWebsitesRequest(**{"query": "Acme", **kwargs})


async def test_official_site_is_verified_only_with_evidence() -> None:
    outcome = await discovery(acme_search()).discover(request(target_names=["Acme"]))

    assert [c.domain for c in outcome.candidates] == ["acme.com"]
    site = outcome.candidates[0]
    assert site.official_status == OfficialStatus.VERIFIED
    assert site.website_type == WebsiteType.OFFICIAL
    assert site.homepage_url == "https://acme.com/"
    assert any("matches the name" in r for r in site.selection_reasons)
    assert site.search_position >= 1 and site.search_query
    assert 0 < site.relevance_score <= 1 and site.discovery_timestamp


async def test_first_result_is_not_assumed_official() -> None:
    search = QuerySearch(
        {
            "Acme": [
                result(
                    1,
                    "https://best-software-list.example.com/acme",
                    "Acme alternatives",
                    "Acme and others",
                ),
                result(
                    2,
                    "https://acme.com/",
                    "Acme Automation - Official Site",
                    "Acme workflows",
                ),
            ]
        }
    )

    outcome = await discovery(search).discover(request(target_names=["Acme"]))
    by_domain = {c.domain: c for c in outcome.candidates}

    assert by_domain["acme.com"].official_status == OfficialStatus.VERIFIED
    assert by_domain["example.com"].official_status == OfficialStatus.UNVERIFIED
    assert outcome.candidates[0].domain == "acme.com"  # ranked above position 1


@pytest.mark.parametrize("domain", ["acme-reviews.example.com", "bestacme.example.com"])
async def test_lookalike_domains_are_not_verified(domain: str) -> None:
    search = QuerySearch(
        {"Acme": [result(1, f"https://{domain}/", "Acme Reviews", "All about Acme")]}
    )

    outcome = await discovery(search).discover(request(target_names=["Acme"]))

    assert outcome.candidates[0].official_status == OfficialStatus.UNVERIFIED
    assert outcome.candidates[0].metadata["official_unverified_reason"]


async def test_third_party_sites_are_excluded_but_noted() -> None:
    outcome = await discovery(acme_search()).discover(request(target_names=["Acme"]))

    assert "g2.com" not in {c.domain for c in outcome.candidates}
    assert any("third-party" in n for n in outcome.notes)
    assert any(
        "g2.com" in r.url for r in outcome.search_results
    )  # raw results preserved


async def test_original_search_results_are_preserved_with_query() -> None:
    outcome = await discovery(acme_search()).discover(request(target_names=["Acme"]))

    assert len(outcome.search_results) >= 3
    assert all(r.metadata["search_query"] for r in outcome.search_results)
    assert {r.position for r in outcome.search_results} == {1, 2, 3}


async def test_duplicate_domains_collapse_to_one_candidate() -> None:
    outcome = await discovery(acme_search()).discover(request(target_names=["Acme"]))

    assert [c.domain for c in outcome.candidates].count("acme.com") == 1
    site = outcome.candidates[0]
    assert "https://acme.com/pricing" in site.metadata["result_urls"]


async def test_unsafe_result_urls_are_rejected_and_counted() -> None:
    search = QuerySearch(
        {
            "Acme": [
                result(1, "http://169.254.169.254/latest", "metadata", ""),
                result(2, "file:///etc/passwd", "file", ""),
                result(3, "https://acme.com/", "Acme Automation", "Acme"),
            ]
        }
    )

    outcome = await discovery(search).discover(request(target_names=["Acme"]))

    assert outcome.rejected_count == 2
    assert [c.domain for c in outcome.candidates] == ["acme.com"]


async def test_hostname_resolving_privately_is_not_a_candidate() -> None:
    search = QuerySearch({"Acme": [result(1, "https://acme.com/", "Acme", "Acme")]})

    outcome = await discovery(search, dns={"acme.com": ["10.1.2.3"]}).discover(
        request(target_names=["Acme"])
    )

    assert outcome.candidates == [] and outcome.rejected_count == 1


async def test_country_and_language_are_forwarded() -> None:
    search = acme_search()

    await discovery(search).discover(
        request(target_names=["Acme"], country="IN", language="EN")
    )

    assert {(c["country"], c["language"]) for c in search.calls} == {("in", "en")}


async def test_industry_query_yields_unverified_candidates() -> None:
    search = QuerySearch(
        {
            "ai automation": [
                result(
                    1,
                    "https://zeta-ai.example.com/",
                    "Zeta AI | Automation",
                    "AI automation",
                )
            ]
        }
    )

    outcome = await discovery(search).discover(
        request(
            query="ai automation companies india", subject_type=SubjectType.INDUSTRY
        )
    )

    assert outcome.candidates[0].official_status == OfficialStatus.UNVERIFIED
    assert outcome.candidates[0].website_name == "Zeta AI"


async def test_max_websites_limits_output() -> None:
    results = [
        result(i, f"https://site{i}.com/", f"Site{i} automation", "")
        for i in range(1, 8)
    ]
    search = QuerySearch({"automation": results})

    outcome = await discovery(search).discover(
        request(query="automation", subject_type=SubjectType.INDUSTRY, max_websites=3)
    )

    assert len(outcome.candidates) == 3


async def test_homepage_branding_can_confirm_an_unverified_site() -> None:
    search = QuerySearch(
        {"Acme": [result(1, "https://acme.com/", "Welcome", "Home page")]}
    )
    d = discovery(search)
    site = (await d.discover(request(target_names=["Acme"]))).candidates[0]
    assert site.official_status == OfficialStatus.UNVERIFIED

    confirmed = d.confirm_with_homepage(
        site, "Acme Automation | Home", "# Acme Automation\n..."
    )
    unrelated = d.confirm_with_homepage(site, "Totally Different", "Nothing here")

    assert confirmed.official_status == OfficialStatus.VERIFIED
    assert unrelated.official_status == OfficialStatus.UNVERIFIED


def test_query_generation() -> None:
    queries = build_queries(request(target_names=["Acme", "Zeta"]))
    assert ("Acme official website", "Acme") in queries and len(queries) == 4
    industry = build_queries(
        request(query="crm tools", subject_type=SubjectType.INDUSTRY)
    )
    assert industry[0] == ("crm tools", None)


async def test_search_provider_errors_propagate() -> None:
    search = QuerySearch(error=ProviderAuthenticationError("SerpApi", "bad key"))

    with pytest.raises(ProviderAuthenticationError):
        await discovery(search).discover(request(target_names=["Acme"]))
