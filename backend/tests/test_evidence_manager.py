"""Tests for claim verification, conflict detection and coverage."""

from app.research.entity_resolver import EntityResolver
from app.research.evidence_manager import EvidenceManager
from app.schemas.intelligence import (
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    OfficialStatus,
    PageType,
    ResearchClaim,
    SourceKind,
    VerificationStatus,
)
from tests.intel_support import HOME_MD, doc

URL = "https://acme.com/"
OFFICIAL = {"acme.com": OfficialStatus.VERIFIED}
HOME_DOC = doc(URL, HOME_MD, PageType.HOMEPAGE, "Acme Automation | Home")


def claim(
    predicate="headquarters",
    value="Bengaluru, India",
    excerpt="headquartered in Bengaluru, India",
    url=URL,
    domain="acme.com",
    **kw,
) -> ResearchClaim:
    return ResearchClaim(
        subject_entity="Acme", subject_type=EntityType.COMPANY, scope_domain="acme.com",
        predicate=predicate, object_value=value, claim_text=f"Acme {predicate}: {value}",
        source_url=url, source_domain=domain, evidence_excerpt=excerpt, **kw,
    )  # fmt: skip


def validate(claims, documents=(HOME_DOC,), official=OFFICIAL):
    return EvidenceManager().validate(list(claims), list(documents), official)


def test_claim_with_excerpt_in_official_source_is_supported() -> None:
    (verified,), conflicts = validate([claim()])

    assert verified.verification_status == VerificationStatus.SUPPORTED
    assert verified.source_kind == SourceKind.OFFICIAL and conflicts == []


def test_excerpt_not_in_source_is_unsupported() -> None:
    (verified,), _ = validate([claim(excerpt="headquartered on the moon")])

    assert verified.verification_status == VerificationStatus.UNSUPPORTED
    assert any("not found" in n for n in verified.notes)


def test_missing_source_content_is_unverified_not_supported() -> None:
    (verified,), _ = validate([claim(url="https://acme.com/never-crawled")])

    assert verified.verification_status == VerificationStatus.UNVERIFIED


def test_search_snippets_are_not_page_evidence() -> None:
    # A claim whose "source" is only a search result (no retrieved page) stays unverified.
    snippet = claim(
        url="https://acme.com/search-result-only", excerpt="Acme automates workflows"
    )

    (verified,), _ = validate([snippet])

    assert verified.verification_status == VerificationStatus.UNVERIFIED


def test_third_party_and_unverified_ownership_are_only_partially_supported() -> None:
    third = {"acme.com": OfficialStatus.THIRD_PARTY}
    unknown = {"acme.com": OfficialStatus.UNVERIFIED}

    (a,), _ = validate([claim()], official=third)
    (b,), _ = validate([claim()], official=unknown)

    assert (
        a.verification_status
        == b.verification_status
        == VerificationStatus.PARTIALLY_SUPPORTED
    )
    assert (
        a.source_kind == SourceKind.THIRD_PARTY and b.source_kind == SourceKind.UNKNOWN
    )
    assert any("third-party" in n for n in a.notes) and any(
        "not verified" in n for n in b.notes
    )


def test_marketing_statements_are_recorded_not_verified_as_fact() -> None:
    excerpt = "Acme Automation is an AI automation company"
    (verified,), _ = validate([claim("description", "AI automation company", excerpt)])

    assert verified.verification_status == VerificationStatus.PARTIALLY_SUPPORTED
    assert any("vendor-stated" in n for n in verified.notes)


def test_llm_extraction_never_counts_as_verified_by_itself() -> None:
    llm = claim(extraction_method=ExtractionMethod.LLM, confidence_score=0.99)
    bad = claim(
        excerpt="made up sentence",
        extraction_method=ExtractionMethod.LLM,
        confidence_score=0.99,
    )

    (ok,), _ = validate([llm])
    (rejected,), _ = validate([bad])

    assert ok.verification_status == VerificationStatus.PARTIALLY_SUPPORTED
    assert (
        rejected.verification_status == VerificationStatus.UNSUPPORTED
    )  # confidence is irrelevant


def test_confidence_does_not_change_the_verification_status() -> None:
    low, high = (validate([claim(confidence_score=c)])[0][0] for c in (0.05, 0.99))

    assert low.verification_status == high.verification_status


def conflict_docs():
    other = doc(
        "https://acme.com/about",
        "# About\n\nAcme is headquartered in Pune, India.\n",
        PageType.ABOUT,
    )
    return [HOME_DOC, other]


def test_conflicting_values_are_all_preserved() -> None:
    claims = [
        claim(),
        claim(
            value="Pune, India",
            excerpt="headquartered in Pune, India",
            url="https://acme.com/about",
        ),
    ]

    verified, conflicts = validate(claims, conflict_docs())

    assert {c.verification_status for c in verified} == {VerificationStatus.CONFLICTING}
    (conflict,) = conflicts
    assert conflict.predicate == "headquarters" and set(conflict.values) == {
        "Bengaluru, India",
        "Pune, India",
    }
    assert len(conflict.claim_ids) == 2
    assert any("official source; preferred" in n for c in verified for n in c.notes)
    assert {c.object_value for c in verified} == {
        "Bengaluru, India",
        "Pune, India",
    }  # nothing dropped


def test_same_value_from_two_pages_is_not_a_conflict() -> None:
    again = doc(
        "https://acme.com/about",
        "# About\n\nHeadquartered in Bengaluru, India since day one.\n",
        PageType.ABOUT,
    )
    claims = [
        claim(),
        claim(
            excerpt="Headquartered in Bengaluru, India", url="https://acme.com/about"
        ),
    ]

    verified, conflicts = validate(claims, [HOME_DOC, again])

    assert conflicts == [] and {c.verification_status for c in verified} == {
        VerificationStatus.SUPPORTED
    }


def price_docs():
    a = doc(
        "https://acme.com/pricing",
        "# Pricing\n\n## Pro\n\nPro: $99 per month\n",
        PageType.PRICING,
    )
    b = doc(
        "https://acme.com/plans",
        "# Plans\n\n## Pro\n\nPro: $79 per month\n",
        PageType.PRICING,
    )
    return [a, b]


def price(url, label, excerpt):
    return claim("price", label, excerpt, url=url)


def test_prices_for_one_plan_conflict_across_pages_only() -> None:
    a, b = price_docs()
    cross = [
        price(a.normalized_url, "Pro: $99 per month", "Pro: $99 per month"),
        price(b.normalized_url, "Pro: $79 per month", "Pro: $79 per month"),
    ]
    same_page = [
        price(a.normalized_url, "Pro: $99 per month", "Pro: $99 per month"),
        price(a.normalized_url, "Pro: $89 per year", "Pro: $99 per month"),
    ]

    verified, conflicts = validate(cross, [a, b])
    _, none = validate(same_page, [a, b])

    assert len(conflicts) == 1 and conflicts[0].predicate == "price:pro"
    assert {c.verification_status for c in verified} == {VerificationStatus.CONFLICTING}
    assert none == []  # several tiers/intervals on a single page are not a conflict


def test_different_plans_do_not_conflict() -> None:
    a, _ = price_docs()
    claims = [
        price(a.normalized_url, "Pro: $99 per month", "Pro: $99 per month"),
        claim(
            "price", "Starter: $9 per month", "Pro: $99 per month", url=a.normalized_url
        ),
    ]

    _, conflicts = validate(claims, [a])

    assert conflicts == []


def test_coverage_metrics() -> None:
    claims = [
        claim(),  # supported
        claim(
            "description",
            "AI automation company",
            "Acme Automation is an AI automation company",
        ),  # partial
        claim(excerpt="nonsense text here"),  # unsupported
        claim(url="https://acme.com/not-retrieved"),  # unverified
    ]
    verified, _ = validate(claims)
    entities = EntityResolver().resolve(
        [
            ExtractedEntity(
                entity_type=EntityType.COMPANY, name="Acme", scope_domain="acme.com"
            )
        ]
    )
    linked = EntityResolver().link_claims(verified, entities)

    coverage = EvidenceManager.coverage(linked, entities)

    assert (
        coverage.total_claims,
        coverage.supported,
        coverage.partially_supported,
    ) == (4, 1, 1)
    assert (coverage.unsupported, coverage.unverified, coverage.conflicting) == (
        1,
        1,
        0,
    )
    assert coverage.coverage_ratio == 0.5
    assert coverage.entities_total == 1 and coverage.entities_with_supported_claim == 1
    assert coverage.pricing_found is False


def test_empty_coverage_is_zero_not_an_error() -> None:
    coverage = EvidenceManager.coverage([], [])

    assert coverage.total_claims == 0 and coverage.coverage_ratio == 0.0


def test_pricing_gaps_are_reported() -> None:
    entities = EntityResolver().resolve(
        [
            ExtractedEntity(
                entity_type=EntityType.PRICING,
                name="Pro: $20",
                scope_domain="acme.com",
                data={
                    "plan_name": "Pro",
                    "price_amount": "20",
                    "currency": "$",
                    "source_url": URL,
                },
            ),
            ExtractedEntity(
                entity_type=EntityType.PRICING,
                name="Full: $5 per month",
                scope_domain="acme.com",
                data={
                    "plan_name": "Full",
                    "price_amount": "5",
                    "currency": "USD",
                    "billing_interval": "month",
                    "source_url": URL,
                },
            ),
        ]
    )

    gaps = EvidenceManager.pricing_gaps(entities)

    assert len(gaps) == 1 and "Pro: $20" in gaps[0] and "billing period" in gaps[0]
