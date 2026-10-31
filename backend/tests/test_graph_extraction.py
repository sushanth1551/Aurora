"""Normalization, entity extraction, resolution, relationships and provenance."""

import pytest

from app.graph.entity_extractor import EntityExtractor
from app.graph.entity_resolver import (
    GraphEntityResolver,
    LLMResolutionAdvisor,
    ResolutionVerdict,
)
from app.graph.graph_schema import Assertion, Label, Rel
from app.graph.models import ResolutionDecision
from app.graph.normalization import (
    company_id_for_domain,
    normalize_domain,
    normalize_text,
    normalize_url,
    source_id_for_url,
)
from app.graph.provenance_manager import build_provenance
from app.graph.relationship_extractor import RelationshipExtractor, RelationshipProposal
from app.schemas.intelligence import EntityType, VerificationStatus
from tests.graph_support import claim, entity, source, website

S = VerificationStatus


def test_normalization_keeps_meaning() -> None:
    assert (
        normalize_text("  Ａｃｍｅ   C++\tSDK ") == "acme c++ sdk"
    )  # NFKC + case + spaces, punctuation kept
    assert normalize_text("Node.js") == "node.js"
    assert normalize_domain("https://WWW.Acme.co.in/pricing") == "acme.co.in"
    assert normalize_domain("docs.acme.com") == "acme.com"
    assert normalize_url("https://www.Acme.com/pricing/") == normalize_url(
        "http://acme.com/pricing"
    )
    assert source_id_for_url("https://acme.com/a/") == source_id_for_url(
        "https://www.acme.com/a"
    )
    assert company_id_for_domain("acme.com") == company_id_for_domain(
        "acme.com"
    )  # deterministic


def acme_material(job: str = "j1"):
    acme = entity(EntityType.COMPANY, "Acme", "acme.com", aliases=["Acme Inc."])
    flow = entity(EntityType.PRODUCT, "Acme Flow", "acme.com")
    claims = [
        claim(acme, "offers_product", "Acme Flow"),
        claim(acme, "headquarters", "Bengaluru, India"),
        claim(acme, "founded_year", "2016", url="https://acme.com/about"),
        claim(
            acme,
            "founded_year",
            "2015",
            url="https://acme.com/press",
            status=S.CONFLICTING,
        ),
        claim(acme, "industry", "AI automation"),
        claim(flow, "feature", "Visual builder"),
        claim(flow, "integration", "Slack"),
        claim(acme, "mentions_technology", "Python"),
        claim(flow, "price", "Starter: $29 per month"),
        claim(acme, "offers_product", "Ghost Product", status=S.UNSUPPORTED),
    ]
    sources = [
        source("https://acme.com/offers_product"),
        source("https://acme.com/about"),
    ]
    return [website("acme.com")], [acme, flow], claims, sources, (acme, flow)


def test_extractor_builds_entities_sources_claims_and_safe_company_facts() -> None:
    websites, entities, claims, sources, (acme, flow) = acme_material()
    ex = EntityExtractor().extract("j1", websites, entities, claims, sources)
    company = ex.entities[str(acme.entity_id)]
    assert company.properties["official_domain"] == "acme.com"
    assert company.properties["headquarters"] == "Bengaluru, India"
    assert "founded_year" not in company.properties  # conflicting values are not picked
    assert company.properties["conflicting_properties"] == ["founded_year"]
    assert company.normalized_name == "acme" and "Acme Inc." in company.aliases
    labels = {e.label for e in ex.entities.values()}
    assert {
        Label.COMPANY,
        Label.PRODUCT,
        Label.FEATURE,
        Label.INTEGRATION,
        Label.TECHNOLOGY,
        Label.TOPIC,
    } <= labels
    # the product named in offers_product is the Module 3 product (same deterministic id)
    subj, obj = ex.claim_links[str(claims[0].claim_id)]
    assert subj == str(acme.entity_id) and obj == str(flow.entity_id)
    assert len(ex.claims) == len(claims) and all(
        c.evidence_id.startswith("c-") for c in ex.claims
    )
    assert "acme.com" in ex.verified_domains
    assert all(s.source_type == "official" for s in ex.sources.values())
    assert any(
        c.source_record_id for c in ex.claims
    )  # linked to the stored research_sources row


async def resolve(job, entities, *, verified, prior=None, advisor=None, evidence=None):
    return await GraphEntityResolver(advisor=advisor).resolve(
        job,
        entities,
        verified_domains=verified,
        prior=prior or [],
        claim_evidence=evidence,
    )


async def test_company_on_verified_domain_gets_domain_id_and_is_stable_across_jobs() -> (
    None
):
    websites, entities, claims, sources, (acme, _) = acme_material()
    ex1 = EntityExtractor().extract("j1", websites, entities, claims, sources)
    map1, nodes1, rec1 = await resolve(
        "j1", ex1.entities.values(), verified=ex1.verified_domains
    )
    company_node = company_id_for_domain("acme.com")
    assert map1[str(acme.entity_id)] == company_node
    first = next(r for r in rec1 if r.entity_key == str(acme.entity_id))
    assert first.stage == "domain" and first.decision is ResolutionDecision.NEW
    # job 2 sees "Acme Automation" on the same verified domain -> same node, recorded as merged
    other = entity(EntityType.COMPANY, "Acme Automation", "acme.com")
    ex2 = EntityExtractor().extract("j2", websites, [other], [], [])
    map2, _, rec2 = await resolve(
        "j2", ex2.entities.values(), verified=ex2.verified_domains, prior=rec1
    )
    assert map2[str(other.entity_id)] == company_node
    assert rec2[0].decision is ResolutionDecision.MERGED and rec2[0].stage == "domain"
    # the very same entity key in a later job is a stable-id match
    map3, _, rec3 = await resolve(
        "j3", ex1.entities.values(), verified=set(), prior=rec1
    )
    assert map3[str(acme.entity_id)] == company_node and rec3[0].stage == "stable_id"


async def test_same_name_on_different_domains_is_review_not_merge() -> None:
    a = entity(EntityType.COMPANY, "Acme", "acme.com")
    b = entity(EntityType.COMPANY, "Acme", "acme.io")
    ex = EntityExtractor().extract(
        "j1",
        [website("acme.com", verified=False), website("acme.io", verified=False)],
        [a, b],
        [],
        [],
    )
    mapping, nodes, records = await resolve("j1", ex.entities.values(), verified=set())
    assert mapping[str(a.entity_id)] != mapping[str(b.entity_id)] and len(nodes) == 2
    review = [r for r in records if r.decision is ResolutionDecision.REVIEW]
    assert len(review) == 1 and "not merged automatically" in review[0].reasons[-1]


async def test_similar_products_of_different_companies_stay_distinct() -> None:
    p1 = entity(EntityType.PRODUCT, "Flow", "acme.com")
    p2 = entity(EntityType.PRODUCT, "Flow", "beta.io")
    ex = EntityExtractor().extract("j1", [], [p1, p2], [], [])
    mapping, nodes, _ = await resolve("j1", ex.entities.values(), verified=set())
    assert len(nodes) == 2 and mapping[str(p1.entity_id)] != mapping[str(p2.entity_id)]


async def test_alias_and_fuzzy_resolution_within_a_website() -> None:
    base = entity(
        EntityType.PRODUCT, "Acme Workflow Studio", "acme.com", aliases=["AWS Studio"]
    )
    ex1 = EntityExtractor().extract("j1", [], [base], [], [])
    _, _, rec1 = await resolve("j1", ex1.entities.values(), verified=set())
    alias = entity(EntityType.PRODUCT, "AWS Studio", "acme.com")
    near = entity(EntityType.PRODUCT, "Acme Workflow Studios", "acme.com")
    maybe = entity(EntityType.PRODUCT, "Acme Workflow Studio Plus", "acme.com")
    ex2 = EntityExtractor().extract("j2", [], [alias, near, maybe], [], [])
    mapping, _, rec2 = await resolve(
        "j2", ex2.entities.values(), verified=set(), prior=rec1
    )
    by_key = {r.entity_key: r for r in rec2}
    node = rec1[0].graph_node_id
    assert (
        mapping[str(alias.entity_id)] == node
        and by_key[str(alias.entity_id)].stage == "alias"
    )
    assert (
        mapping[str(near.entity_id)] == node
        and by_key[str(near.entity_id)].stage == "fuzzy"
    )
    assert mapping[str(maybe.entity_id)] != node  # ambiguous: kept separate
    assert by_key[str(maybe.entity_id)].decision is ResolutionDecision.REVIEW


async def test_merges_keep_aliases_sources_and_jobs() -> None:
    a = entity(EntityType.COMPANY, "Acme", "acme.com")
    b = entity(EntityType.COMPANY, "Acme Corp", "acme.com", aliases=["ACME"])
    ex = EntityExtractor().extract("j1", [website("acme.com")], [a, b], [], [])
    _, nodes, _ = await resolve(
        "j1", ex.entities.values(), verified=ex.verified_domains
    )
    (company,) = nodes
    assert {"Acme Corp", "ACME"} <= set(company.aliases) | {company.canonical_name}
    assert set(company.source_record_ids) == {str(a.entity_id), str(b.entity_id)}


class FakeLLM:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    async def structured(self, prompt, schema, **kw):
        self.calls += 1
        assert "<record" in prompt and schema is ResolutionVerdict
        return self.verdict


@pytest.mark.parametrize(
    ("verdict", "merged"),
    [
        (
            ResolutionVerdict(
                same_entity=True, confidence=0.95, evidence_claim_ids=["c1"]
            ),
            True,
        ),
        (
            ResolutionVerdict(
                same_entity=True, confidence=0.6, evidence_claim_ids=["c1"]
            ),
            False,
        ),  # below threshold
        (
            ResolutionVerdict(
                same_entity=True, confidence=0.99, evidence_claim_ids=["made-up"]
            ),
            False,
        ),  # no real evidence
        (
            ResolutionVerdict(
                same_entity=False, confidence=0.99, evidence_claim_ids=["c1"]
            ),
            False,
        ),
    ],
)
async def test_llm_resolution_only_merges_above_threshold_with_cited_evidence(
    verdict, merged
) -> None:
    base = entity(EntityType.PRODUCT, "Acme Workflow Studio", "acme.com")
    ex1 = EntityExtractor().extract("j1", [], [base], [], [])
    _, _, rec1 = await resolve("j1", ex1.entities.values(), verified=set())
    maybe = entity(EntityType.PRODUCT, "Acme Workflow Studio Plus", "acme.com")
    ex2 = EntityExtractor().extract("j2", [], [maybe], [], [])
    llm = FakeLLM(verdict)
    evidence = {
        str(maybe.entity_id): [
            {"claim_id": "c1", "text": "Acme Workflow Studio Plus is the same product"}
        ]
    }
    mapping, _, rec = await resolve("j2", ex2.entities.values(), verified=set(), prior=rec1,
                                    advisor=LLMResolutionAdvisor(llm), evidence=evidence)  # fmt: skip
    assert llm.calls == 1
    assert (mapping[str(maybe.entity_id)] == rec1[0].graph_node_id) is merged
    assert (rec[0].stage == "llm") is merged


async def test_relationships_are_validated_merged_and_provenanced() -> None:
    websites, entities, claims, sources, (acme, flow) = acme_material()
    duplicate = claim(
        acme, "offers_product", "Acme Flow", url="https://acme.com/products"
    )
    claims.append(duplicate)
    claims.append(claim(flow, "offers_product", "Something"))  # product cannot OFFER
    ex = EntityExtractor().extract("j1", websites, entities, claims, sources)
    mapping, nodes, _ = await resolve(
        "j1", ex.entities.values(), verified=ex.verified_domains
    )
    links = {
        cid: (mapping.get(s or "", s), mapping.get(o or "", o))
        for cid, (s, o) in ex.claim_links.items()
    }
    node_map = {n.id: n for n in nodes}
    rows, rejected = RelationshipExtractor().extract(ex.claims, links, node_map)
    by_type = {}
    for r in rows:
        by_type.setdefault(r.type, []).append(r)
    (offers,) = by_type[Rel.OFFERS]
    assert (
        len(offers.claim_ids) == 2 and len(offers.source_ids) == 2
    )  # both supporting claims kept
    uses = by_type[Rel.USES][0]
    assert (
        uses.assertion is Assertion.INFERRED and uses.basis
    )  # mention != proof of use
    assert by_type[Rel.HAS_FEATURE][0].source_label is Label.PRODUCT
    assert by_type[Rel.OPERATES_IN][0].target_label is Label.TOPIC
    reasons = " | ".join(r.reason for r in rejected)
    assert "excerpt not found" in reasons  # unsupported claim -> no fact edge
    assert "cannot be the source of OFFERS" in reasons
    assert all(r.claim_ids for r in rows)  # every fact edge carries evidence

    prov, linked = build_provenance(ex.claims, links, node_map)
    supports = [p for p in prov if p.type is Rel.SUPPORTS]
    assert len(supports) == len(ex.claims)
    unsupported = next(p for p in supports if p.verification_status == "unsupported")
    assert unsupported.excerpt_verified is False  # kept, but not treated as verified
    roles = {p.role for p in prov if p.type is Rel.MENTIONS}
    assert roles == {"subject", "object"} and linked == len(ex.claims)


async def test_competitors_are_inferred_only_from_shared_verified_industry() -> None:
    a = entity(EntityType.COMPANY, "Acme", "acme.com")
    b = entity(EntityType.COMPANY, "Beta", "beta.io")
    c = entity(EntityType.COMPANY, "Gamma", "gamma.dev")
    claims = [
        claim(a, "industry", "AI automation"),
        claim(b, "industry", "AI Automation"),  # same topic after normalization
        claim(
            c, "industry", "AI automation", status=S.UNVERIFIED
        ),  # not verified: no inference
    ]
    ex = EntityExtractor().extract(
        "j1",
        [website("acme.com"), website("beta.io"), website("gamma.dev")],
        [a, b, c],
        claims,
        [],
    )
    mapping, nodes, _ = await resolve(
        "j1", ex.entities.values(), verified=ex.verified_domains
    )
    links = {
        cid: (mapping.get(s or "", s), mapping.get(o or "", o))
        for cid, (s, o) in ex.claim_links.items()
    }
    rows, _ = RelationshipExtractor().extract(
        ex.claims, links, {n.id: n for n in nodes}
    )
    comp = [r for r in rows if r.type is Rel.COMPETES_WITH]
    assert len(comp) == 1
    edge = comp[0]
    assert (
        edge.assertion is Assertion.INFERRED and edge.verification_status == "inferred"
    )
    assert edge.basis.startswith("same_industry:") and len(edge.claim_ids) == 2
    assert edge.source_id < edge.target_id  # symmetric edges are stored once, ordered


def test_proposals_need_existing_claims_and_valid_types() -> None:
    from app.graph.models import ClaimNode, GraphEntity

    def node(i, label):
        return GraphEntity(
            id=i, label=label, canonical_name=i, normalized_name=i, match_key=i
        )

    nodes = {
        "c": node("c", Label.COMPANY),
        "p": node("p", Label.PRODUCT),
        "t": node("t", Label.TECHNOLOGY),
    }
    from datetime import UTC, datetime

    claims = {"k1": ClaimNode(id="k1", evidence_id="c-1", research_job_id="j", subject="c", predicate="x",
                              object_value="p", claim_text="t", evidence_excerpt="t", source_url="u",
                              verification_status="supported", extraction_method="llm",
                              observed_at=datetime.now(UTC))}  # fmt: skip
    rows, rejected = RelationshipExtractor().validate_proposals(
        [
            RelationshipProposal(
                type=Rel.OFFERS,
                source_id="c",
                target_id="p",
                claim_ids=["k1"],
                assertion=Assertion.DIRECT,
            ),
            RelationshipProposal(
                type=Rel.OFFERS,
                source_id="c",
                target_id="p",
                claim_ids=["invented"],
                assertion=Assertion.DIRECT,
            ),
            RelationshipProposal(
                type=Rel.HAS_FEATURE, source_id="c", target_id="t", claim_ids=["k1"]
            ),
            RelationshipProposal(
                type=Rel.OFFERS,
                source_id="c",
                target_id="p",
                claim_ids=["k1"],
                assertion=Assertion.INFERRED,
            ),
        ],
        nodes,
        claims,
    )
    assert len(rows) == 1 and rows[0].verification_status == "supported"
    reasons = [r.reason for r in rejected]
    assert "no existing claim supports the proposal" in reasons
    assert any("cannot be the source" in r for r in reasons)
    assert "OFFERS cannot be inferred" in reasons
