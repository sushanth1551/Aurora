"""Tests for website mapping, page classification and scoring."""

import pytest

from app.core.config import Settings
from app.research.source_manager import SourceManager
from app.research.website_mapper import (
    RuleBasedPageClassifier,
    WebsiteMapper,
    exclusion_reason,
    strip_tracking,
)
from app.schemas.intelligence import PageType
from app.tools.errors import ProviderUnavailableError
from app.tools.firecrawl_tool import MapLink
from tests.fakes import make_policy
from tests.intel_support import SITE, FakeMapTool


@pytest.mark.parametrize(
    ("url", "title", "expected"),
    [
        ("https://acme.com/", None, PageType.HOMEPAGE),
        ("https://acme.com/about-us", None, PageType.ABOUT),
        ("https://acme.com/company/team", None, PageType.COMPANY),
        ("https://acme.com/products/flow", None, PageType.PRODUCT),
        ("https://acme.com/services/consulting", None, PageType.SERVICE),
        ("https://acme.com/pricing", None, PageType.PRICING),
        ("https://acme.com/plans", None, PageType.PRICING),
        ("https://acme.com/features", None, PageType.FEATURES),
        ("https://acme.com/docs/getting-started", None, PageType.DOCUMENTATION),
        ("https://docs.acme.com/", None, PageType.DOCUMENTATION),
        ("https://acme.com/api-reference/v1", None, PageType.API_DOCUMENTATION),
        ("https://api.acme.com/", None, PageType.API_DOCUMENTATION),
        ("https://acme.com/blog/launch-post", None, PageType.BLOG),
        ("https://acme.com/case-studies/globex", None, PageType.CASE_STUDY),
        ("https://acme.com/customers", None, PageType.CUSTOMER_STORY),
        ("https://acme.com/integrations", None, PageType.INTEGRATIONS),
        ("https://acme.com/security", None, PageType.SECURITY),
        ("https://acme.com/careers", None, PageType.CAREERS),
        ("https://acme.com/contact-us", None, PageType.CONTACT),
        ("https://acme.com/x1y2", None, PageType.UNKNOWN),
        (
            "https://acme.com/x1y2",
            "Our Pricing Plans",
            PageType.PRICING,
        ),  # title fallback
    ],
)
def test_rule_based_classification(
    url: str, title: str | None, expected: PageType
) -> None:
    assert RuleBasedPageClassifier().classify(url, title) == expected


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://acme.com/logo.png", "non_html_asset"),
        ("https://acme.com/brochure.pdf", "non_html_asset"),
        ("https://acme.com/login", "authentication_page"),
        ("https://acme.com/my-account/orders", "authentication_page"),
        ("https://acme.com/cart", "transactional_page"),
        ("https://acme.com/wp-admin/x", "admin_page"),
        ("https://acme.com/tag/ai", "taxonomy_archive"),
        ("https://acme.com/blog/page/3", "pagination"),
        ("https://acme.com/pricing", None),
    ],
)
def test_exclusion_rules(url: str, reason: str | None) -> None:
    assert exclusion_reason(url) == reason


def test_tracking_parameters_are_stripped_but_real_ones_kept() -> None:
    assert (
        strip_tracking("https://a.com/p?utm_source=x&id=7&gclid=1")
        == "https://a.com/p?id=7"
    )
    assert strip_tracking("https://a.com/p") == "https://a.com/p"


def mapper(tool, dns=None, **settings) -> WebsiteMapper:
    return WebsiteMapper(
        tool, SourceManager(make_policy(dns)), settings=Settings(**settings)
    )


async def test_map_classifies_scores_and_excludes() -> None:
    tool = FakeMapTool(
        {
            f"{SITE}/": [
                MapLink(url=f"{SITE}/pricing", title="Pricing"),
                f"{SITE}/product/flow",
                f"{SITE}/about",
                f"{SITE}/blog/hello",
                f"{SITE}/login",
                f"{SITE}/logo.png",
                f"{SITE}/pricing?utm_source=ad",  # duplicate after tracking strip
                "https://other-site.com/page",  # outside the website
                "http://10.0.0.5/admin",  # unsafe
                f"{SITE}/a/b/c/d/e",  # too deep
            ]
        }
    )

    result = await mapper(tool).map(f"{SITE}/", "acme pricing")
    pages = {p.normalized_url: p for p in result.pages}

    assert pages[f"{SITE}/"].page_type == PageType.HOMEPAGE
    assert pages[f"{SITE}/pricing"].page_type == PageType.PRICING
    assert len([p for p in result.pages if p.normalized_url == f"{SITE}/pricing"]) == 1
    assert pages[f"{SITE}/login"].exclusion_reason == "authentication_page"
    assert pages[f"{SITE}/logo.png"].exclusion_reason == "non_html_asset"
    assert (
        pages["https://other-site.com/page"].exclusion_reason == "outside_website_scope"
    )
    assert pages["http://10.0.0.5/admin"].exclusion_reason.startswith("url_rejected")
    assert pages[f"{SITE}/a/b/c/d/e"].exclusion_reason == "exceeds_max_depth"
    assert result.discovered_urls == 10
    best = result.eligible[0]
    assert best.page_type in (PageType.PRICING, PageType.PRODUCT, PageType.HOMEPAGE)
    assert (
        pages[f"{SITE}/pricing"].relevance_score
        > pages[f"{SITE}/blog/hello"].relevance_score
    )
    assert all(p.mapped_at and p.discovery_method for p in result.pages)


async def test_map_respects_limits_and_passes_search() -> None:
    tool = FakeMapTool({"*": [f"{SITE}/p{i}" for i in range(50)]})

    result = await mapper(tool).map(f"{SITE}/", "q", max_urls=5, search="pricing")

    assert tool.calls[0]["limit"] == 5 and tool.calls[0]["search"] == "pricing"
    assert len(result.pages) <= 6  # homepage + at most 5 links


async def test_map_cap_is_the_configured_maximum() -> None:
    tool = FakeMapTool({"*": []})

    await mapper(tool, website_research_max_map_urls=30).map(
        f"{SITE}/", "q", max_urls=1000
    )

    assert tool.calls[0]["limit"] == 30


async def test_depth_limit_is_configurable() -> None:
    tool = FakeMapTool({"*": [f"{SITE}/docs/a/b"]})

    shallow = await mapper(tool).map(f"{SITE}/", "q", max_depth=1)
    deep = await mapper(tool).map(f"{SITE}/", "q", max_depth=3)

    pick = lambda r: next(p for p in r.pages if p.normalized_url.endswith("/docs/a/b"))  # noqa: E731
    assert pick(shallow).exclusion_reason == "exceeds_max_depth"
    assert pick(deep).crawl_eligible


async def test_subdomains_of_the_site_are_in_scope_but_checked_for_safety() -> None:
    tool = FakeMapTool(
        {"*": ["https://docs.acme.com/intro", "https://evil.acme.com/x"]}
    )

    result = await mapper(tool, dns={"evil.acme.com": ["10.0.0.9"]}).map(
        f"{SITE}/", "q"
    )
    pages = {p.normalized_url: p for p in result.pages}

    assert pages["https://docs.acme.com/intro"].crawl_eligible
    assert pages["https://docs.acme.com/intro"].page_type == PageType.DOCUMENTATION
    assert (
        pages["https://evil.acme.com/x"].exclusion_reason == "url_rejected:blocked_ip"
    )


async def test_unsafe_root_url_is_refused_before_calling_the_provider() -> None:
    tool = FakeMapTool()

    with pytest.raises(Exception, match="non-public|Non-public|blocked"):
        await mapper(tool).map("http://127.0.0.1/", "q")

    assert tool.calls == []


async def test_provider_errors_propagate() -> None:
    tool = FakeMapTool(error=ProviderUnavailableError("Firecrawl", "down"))

    with pytest.raises(ProviderUnavailableError):
        await mapper(tool).map(f"{SITE}/", "q")


async def test_custom_classifier_can_be_injected() -> None:
    class AlwaysPricing:
        def classify(self, url: str, title: str | None = None) -> PageType:
            return PageType.PRICING

    tool = FakeMapTool({"*": [f"{SITE}/whatever"]})
    m = WebsiteMapper(tool, SourceManager(make_policy()), classifier=AlwaysPricing())

    result = await m.map(f"{SITE}/", "q")

    assert {p.page_type for p in result.pages} == {PageType.PRICING}
