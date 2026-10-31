"""Context builder: Module 2/3 retrieval, dedup, ranking, limits and provenance."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.exceptions import ResourceNotFoundException
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.intelligence.context_builder import (
    KeywordRetriever,
    freshness,
    normalize_url,
    score_source,
)
from app.intelligence.models import EvidenceKind, Tier
from app.intelligence.text import contains_excerpt
from app.schemas.intelligence import SourceKind, VerificationStatus
from app.schemas.research import utc_now
from tests.analysis_support import builder, ev, seed_acme_job


async def test_context_combines_module2_sources_and_module3_claims(db_session) -> None:
    job_id = await seed_acme_job(db_session)
    claims = await IntelligenceRepository(db_session).list_claims(job_id, limit=1000)

    ctx = await builder(db_session).build([job_id], "Acme pricing and products")

    claim_items = [e for e in ctx.evidence if e.kind is EvidenceKind.CLAIM]
    excerpt_items = [e for e in ctx.evidence if e.kind is EvidenceKind.SOURCE_EXCERPT]
    assert claim_items and excerpt_items
    assert ctx.statistics["claims_loaded"] == claims.total
    assert "Acme" in " ".join(ctx.companies)
    ids = {c.claim_id for c in claims.items}
    for item in claim_items:
        # stable id derived from the stored claim id; provenance preserved
        assert (
            item.claim_id in ids and item.evidence_id == f"c-{item.claim_id.hex[:10]}"
        )
        assert item.source_url.startswith("https://acme.com") and item.excerpt
        assert item.research_job_id == job_id
        assert item.observed_at is not None
    for item in excerpt_items:
        # excerpts are verbatim slices of the stored page text
        assert contains_excerpt(ctx.source_texts[item.source_url], item.excerpt)
    assert all(
        s.kind is SourceKind.OFFICIAL for s in ctx.sources if "acme.com" in s.url
    )
    assert ctx.context_hash and ctx.token_estimate > 0


async def test_rebuild_is_deterministic_and_ids_are_stable(db_session) -> None:
    job_id = await seed_acme_job(db_session)
    b = builder(db_session)
    first = await b.build([job_id], "Acme pricing")
    second = await b.build([job_id], "Acme pricing", version=2)
    assert [e.evidence_id for e in first.evidence] == [
        e.evidence_id for e in second.evidence
    ]
    assert first.context_hash == second.context_hash and second.version == 2


async def test_verified_unverified_and_missing_are_separated(db_session) -> None:
    job_id = await seed_acme_job(db_session)
    ctx = await builder(db_session).build([job_id], "Acme")
    by_id = ctx.evidence_by_id()
    assert ctx.verified_ids
    assert all(
        by_id[i].verification_status
        in (VerificationStatus.SUPPORTED, VerificationStatus.PARTIALLY_SUPPORTED)
        for i in ctx.verified_ids
    )
    assert not set(ctx.verified_ids) & set(ctx.unverified_ids)
    # every gap is a "not publicly found" statement about a known company
    for gap in ctx.missing_information:
        assert gap.related_entity in ctx.companies and "found" in gap.reason


async def test_context_token_and_source_limits(db_session) -> None:
    job_id = await seed_acme_job(db_session)
    full = await builder(db_session).build([job_id], "Acme")
    small = await builder(
        db_session, Settings(intelligence_context_max_tokens=500)
    ).build([job_id], "Acme")
    one = await builder(db_session).build([job_id], "Acme", max_sources=1)
    assert small.token_estimate <= 500 < full.token_estimate
    assert small.statistics["truncated"] is True
    assert len({e.source_url for e in one.evidence}) == 1
    # the highest-ranked evidence survives truncation
    assert small.evidence[0].evidence_id == full.evidence[0].evidence_id


async def test_missing_job_is_reported(db_session) -> None:
    with pytest.raises(ResourceNotFoundException):
        await builder(db_session).build([uuid4()], "Acme")


async def test_duplicate_sources_and_claims_are_removed(db_session) -> None:
    job_id = await seed_acme_job(db_session)
    # building over the same job twice simulates duplicated evidence across jobs
    ctx = await builder(db_session).build([job_id, job_id], "Acme")
    single = await builder(db_session).build([job_id], "Acme")
    assert ctx.statistics["duplicate_sources_removed"] >= len(single.sources) - 1
    assert ctx.statistics["duplicate_claims_removed"] > 0
    assert len({e.evidence_id for e in ctx.evidence}) == len(ctx.evidence)


def test_source_credibility_is_explainable() -> None:
    docs = score_source(
        url="https://acme.com/docs/api",
        domain="acme.com",
        kind=SourceKind.OFFICIAL,
        snippet_only=False,
    )
    pricing = score_source(
        url="https://acme.com/pricing",
        domain="acme.com",
        kind=SourceKind.OFFICIAL,
        snippet_only=False,
    )
    home = score_source(
        url="https://acme.com/",
        domain="acme.com",
        kind=SourceKind.OFFICIAL,
        snippet_only=False,
    )
    directory = score_source(
        url="https://www.g2.com/p/acme",
        domain="www.g2.com",
        kind=SourceKind.THIRD_PARTY,
        snippet_only=False,
    )
    snippet = score_source(
        url="https://acme.com/",
        domain="acme.com",
        kind=SourceKind.OFFICIAL,
        snippet_only=True,
    )
    gov = score_source(
        url="https://mca.gov.in/x",
        domain="mca.gov.in",
        kind=SourceKind.UNKNOWN,
        snippet_only=False,
    )
    assert docs.tier is Tier.OFFICIAL_DOCS and pricing.tier is Tier.OFFICIAL_PRICING
    assert home.score < docs.score and directory.tier is Tier.DIRECTORY
    assert snippet.tier is Tier.SNIPPET and snippet.score < directory.score < home.score
    assert gov.tier is Tier.GOVERNMENT
    assert "self-reported" in " ".join(home.factors)  # marketing is not verification
    assert all(c.factors for c in (docs, pricing, home, directory, snippet, gov))


def test_freshness_and_relevance() -> None:
    now = utc_now()
    assert freshness(now, now, 365) == 1.0
    assert freshness(now - timedelta(days=400), now, 365) == 0.0
    assert freshness(None, now, 365) == 0.5
    items = [
        ev("c-1", "Acme", "price", "$29 per month"),
        ev("c-2", "Acme", "headquarters", "Pune"),
    ]
    scores = KeywordRetriever().score("Acme pricing per month", items)
    assert scores["c-1"] > scores["c-2"]


def test_url_normalization() -> None:
    assert normalize_url("https://WWW.Acme.com/pricing/#x") == normalize_url(
        "https://acme.com/pricing"
    )
