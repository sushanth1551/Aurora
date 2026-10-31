"""Tests for deterministic report building and rendering."""

import pytest

from app.research.entity_resolver import EntityResolver
from app.research.evidence_manager import EvidenceManager
from app.research.research_report import ReportBuilder, render_markdown
from app.research.structured_extractor import DeterministicExtractor, WebsiteContext
from app.schemas.intelligence import (
    NOT_FOUND,
    OfficialStatus,
    PageType,
    ReportType,
    WebsiteCandidate,
)
from tests.intel_support import ABOUT_MD, HOME_MD, PRICING_MD, PRODUCT_MD, doc

OFFICIAL = {"acme.com": OfficialStatus.VERIFIED}


def candidate(
    name="Acme", domain="acme.com", status=OfficialStatus.VERIFIED
) -> WebsiteCandidate:
    return WebsiteCandidate(
        website_name=name, domain=domain, homepage_url=f"https://{domain}/", search_query=name,
        search_position=1, relevance_score=0.9, official_status=status, confidence=0.8,
    )  # fmt: skip


def pipeline(pages, site=None, official=OFFICIAL):
    """Run extraction -> resolution -> evidence for a list of (path, md, type) pages."""
    site = site or candidate()
    documents = [doc(f"https://{site.domain}/{p}", md, t) for p, md, t in pages]
    ctx = WebsiteContext(
        site.website_name, site.domain, site.homepage_url, site.official_status
    )
    extracted = DeterministicExtractor().extract(ctx, documents)
    resolver = EntityResolver()
    entities = resolver.resolve(extracted.entities)
    claims, conflicts = EvidenceManager().validate(
        resolver.link_claims(extracted.claims, entities), documents, official
    )
    return site, entities, claims, conflicts, extracted


FULL = [
    ("", HOME_MD, PageType.HOMEPAGE),
    ("about", ABOUT_MD, PageType.ABOUT),
    ("pricing", PRICING_MD, PageType.PRICING),
    ("product/flow", PRODUCT_MD, PageType.PRODUCT),
]


def build(pages=FULL, report_type=ReportType.COMPANY_PROFILE, sites=None, **kw):
    site, entities, claims, conflicts, extracted = pipeline(pages)
    coverage = EvidenceManager.coverage(claims, entities)
    return ReportBuilder().build(
        report_type,
        "Acme research",
        sites or [site],
        entities,
        claims,
        conflicts,
        coverage,
        {"acme.com": extracted.pricing_pages_checked},
        **kw,
    )


def test_company_report_contains_evidenced_fields_and_sources() -> None:
    report = build()
    entry = report.data.companies[0]

    assert entry.name == "Acme" and entry.official_website == "https://acme.com/"
    assert entry.official_status == OfficialStatus.VERIFIED
    assert entry.headquarters == "Bengaluru, India" and entry.founded_year == "2016"
    assert entry.company_size == "250 employees" and entry.industry == "AI automation"
    assert (
        entry.products == ["Acme Flow"] and "Visual workflow builder" in entry.features
    )
    assert "Slack" in entry.integrations and "Invoice processing" in entry.use_cases
    assert entry.target_customers == ["enterprises", "startups"]
    assert "Python (mentioned on the website)" in entry.technologies
    assert entry.pricing_status == "found" and any(
        "$29" in p and "per month" in p for p in entry.pricing
    )
    assert all(
        "source: https://acme.com/pricing" in p and "verified" in p
        for p in entry.pricing
    )
    assert {s.url for s in report.data.sources} >= {
        "https://acme.com/",
        "https://acme.com/pricing",
    }
    assert report.data.evidence_coverage.total_claims > 0
    assert report.data.research_date


def test_unknown_fields_say_not_publicly_found_and_nothing_is_fabricated() -> None:
    report = build([("", "# Welcome\n\nWe build software.\n", PageType.HOMEPAGE)])
    entry = report.data.companies[0]

    for value in (
        entry.headquarters,
        entry.founded_year,
        entry.company_size,
        entry.industry,
    ):
        assert value == NOT_FOUND
    assert entry.strengths == [] and entry.limitations == []  # never invented
    assert (
        entry.pricing == []
        and entry.pricing_status == f"{NOT_FOUND} (no pricing page was crawled)"
    )
    md = report.markdown
    for forbidden in ("market share", "revenue", "customers worldwide", "ranked #"):
        assert forbidden not in md.lower()
    assert NOT_FOUND in md


def test_pricing_page_without_prices_reports_not_publicly_found() -> None:
    report = build(
        [("pricing", "# Pricing\n\nFlexible plans for every team.\n", PageType.PRICING)]
    )

    assert (
        report.data.companies[0].pricing_status
        == f"{NOT_FOUND} (checked 1 pricing page(s))"
    )


def test_conflicts_and_unverified_information_are_surfaced() -> None:
    pages = [
        ("", HOME_MD, PageType.HOMEPAGE),
        (
            "contact",
            "# Contact\n\nOur head office is in Mumbai, India.\n",
            PageType.CONTACT,
        ),
    ]

    report = build(pages)
    entry = report.data.companies[0]

    assert entry.headquarters.startswith("conflicting evidence:")
    assert (
        "Bengaluru, India" in entry.headquarters
        and "Mumbai, India" in entry.headquarters
    )
    assert any(
        "headquarters" in c and "Mumbai" in c
        for c in report.data.conflicting_information
    )
    assert (
        "## Conflicting information" in report.markdown and "Mumbai" in report.markdown
    )


def test_unverified_ownership_is_listed() -> None:
    site = candidate(status=OfficialStatus.UNVERIFIED)
    _, entities, claims, conflicts, _ = pipeline(
        FULL, site=site, official={"acme.com": OfficialStatus.UNVERIFIED}
    )
    coverage = EvidenceManager.coverage(claims, entities)

    report = ReportBuilder().build(
        ReportType.COMPANY_PROFILE, "x", [site], entities, claims, conflicts, coverage
    )

    assert any("could not be verified" in u for u in report.data.unverified_information)
    assert report.data.companies[0].official_status == OfficialStatus.UNVERIFIED


def test_markdown_is_rendered_deterministically_from_structured_data() -> None:
    report = build()

    assert render_markdown(report.data) == report.markdown
    assert report.markdown.startswith("# Company Profile: Acme research")
    for heading in (
        "### Products",
        "### Pricing information",
        "## Evidence coverage",
        "## Sources",
        "## Notes",
    ):
        assert heading in report.markdown
    assert report.data.model_dump_json()  # structured JSON is serializable


def test_competitor_report_compares_without_asserting_relationships() -> None:
    a, ea, ca, cfa, xa = pipeline(FULL)
    b_site = candidate("Globex", "globex.com")
    b, eb, cb, cfb, xb = pipeline(
        [("", "# Globex\n\nGlobex is a robotics company.\n", PageType.HOMEPAGE)],
        site=b_site,
        official={"globex.com": OfficialStatus.VERIFIED},
    )
    entities, claims = ea + eb, ca + cb
    coverage = EvidenceManager.coverage(claims, entities)

    report = ReportBuilder().build(
        ReportType.COMPETITOR_PROFILE,
        "AI tools",
        [a, b],
        entities,
        claims,
        cfa + cfb,
        coverage,
        {"acme.com": 1, "globex.com": 0},
    )

    rows = {r["name"]: r for r in report.data.comparison}
    assert set(rows) == {"Acme", "Globex"}
    assert (
        rows["Acme"]["products"] == ["Acme Flow"] and rows["Globex"]["products"] == []
    )
    assert rows["Globex"]["documented_differentiators"] == [NOT_FOUND]
    relations = report.data.companies[0].competitor_relationships[0]
    assert "no competitive relationship is asserted" in relations
    assert "| name |" in report.markdown and "Researched alongside" in report.markdown


@pytest.mark.parametrize(
    "report_type",
    [
        ReportType.PRODUCT_PROFILE,
        ReportType.SERVICE_PROFILE,
        ReportType.TECHNOLOGY_PROFILE,
        ReportType.INDUSTRY_REPORT,
    ],
)
def test_all_report_types_render(report_type) -> None:
    report = build(report_type=report_type)

    assert report.report_type == report_type and report.markdown
    assert report.data.comparison is not None


def test_product_profile_rows() -> None:
    report = build(report_type=ReportType.PRODUCT_PROFILE)

    (row,) = report.data.comparison
    assert row["product"] == "Acme Flow" and row["company"] == "Acme"
    assert (
        row["features"]
        and row["official_product_url"] == "https://acme.com/product/flow"
    )
    assert row["category"] == NOT_FOUND


def test_technology_profile_lists_named_technologies() -> None:
    report = build(report_type=ReportType.TECHNOLOGY_PROFILE)

    assert {"Python", "Kubernetes"} <= {r["technology"] for r in report.data.comparison}
