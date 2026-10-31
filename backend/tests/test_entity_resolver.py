"""Tests for conservative, explainable entity resolution."""

import pytest

from app.research.entity_resolver import (
    EntityResolver,
    entity_id_for,
    normalize_entity_name,
)
from app.schemas.intelligence import (
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    ResearchClaim,
)


def ent(
    kind: EntityType, name: str, scope: str | None = "acme.com", **data
) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=kind, name=name, data=data, source_urls=[f"https://{scope or 'x.com'}/{name[:3]}"],
        scope_domain=scope, confidence=0.6,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "a", "b"),
    [
        (EntityType.COMPANY, "Acme Technologies Pvt. Ltd.", "acme technologies"),
        (EntityType.COMPANY, "ACME, Inc.", "Acme"),
        (EntityType.PRODUCT, "Acme  Flow", "acme-flow"),
        (EntityType.TECHNOLOGY, "Node.js", "NodeJS"),
        (EntityType.TECHNOLOGY, "Postgres", "PostgreSQL"),
        (EntityType.TECHNOLOGY, "K8s", "Kubernetes"),
        (EntityType.TARGET_CUSTOMER, "SMBs", "Small and medium businesses"),
    ],
)
def test_names_normalize_equivalently(kind, a, b) -> None:
    assert normalize_entity_name(kind, a) == normalize_entity_name(kind, b)


def test_duplicate_products_on_one_website_merge_with_reasons_and_aliases() -> None:
    entities = [
        ent(EntityType.PRODUCT, "Acme Flow", description="Build flows", features=["A"]),
        ent(
            EntityType.PRODUCT, "ACME FLOW", features=["A", "B"], category="Automation"
        ),
        ent(EntityType.PRODUCT, "acme flow", description="Different words"),
    ]

    (merged,) = EntityResolver().resolve(entities)

    assert merged.canonical_name in {"Acme Flow", "ACME FLOW", "acme flow"}
    assert set(merged.original_names) == {"Acme Flow", "ACME FLOW", "acme flow"}
    assert len(merged.aliases) == 2 and merged.canonical_name not in merged.aliases
    assert (
        merged.attributes["features"] == ["A", "B"]
        and merged.attributes["category"] == "Automation"
    )
    assert merged.description == "Build flows"
    assert merged.attributes["_alternatives"]["description"] == [
        "Different words"
    ]  # conflict preserved
    assert any("merged 3 mentions" in r for r in merged.match_reasons)
    assert len(merged.source_urls) >= 1


def test_same_name_on_different_websites_is_never_merged() -> None:
    a = ent(EntityType.PRODUCT, "Flow", "acme.com")
    b = ent(EntityType.PRODUCT, "Flow", "globex.com")

    resolved = EntityResolver().resolve([a, b])

    assert len(resolved) == 2 and len({e.entity_id for e in resolved}) == 2
    assert any("different website" in r for e in resolved for r in e.match_reasons)


def test_technologies_are_shared_across_websites() -> None:
    a = ent(EntityType.TECHNOLOGY, "Python", "acme.com")
    b = ent(EntityType.TECHNOLOGY, "python", "globex.com")

    (tech,) = EntityResolver().resolve([a, b])

    assert tech.scope_domain is None and len(tech.source_urls) == 2
    assert "across websites" in tech.match_reasons[0]


def test_near_identical_names_merge_but_uncertain_ones_stay_separate() -> None:
    near = [
        ent(EntityType.PRODUCT, "Workflow Builder Pro"),
        ent(EntityType.PRODUCT, "Workflow Builder Pro."),
    ]
    uncertain = [
        ent(EntityType.PRODUCT, "Acme Studio"),
        ent(EntityType.PRODUCT, "Acme Studios Plus"),
    ]
    distinct = [
        ent(EntityType.PRODUCT, "Acme Studio"),
        ent(EntityType.PRODUCT, "Acme Cloud"),
    ]

    assert len(EntityResolver().resolve(near)) == 1
    separate = EntityResolver().resolve(uncertain)
    assert len(separate) == 2
    assert any("possible duplicate" in r for e in separate for r in e.match_reasons)
    assert len(EntityResolver().resolve(distinct)) == 2


def test_entity_ids_are_stable_across_runs() -> None:
    first = EntityResolver().resolve([ent(EntityType.COMPANY, "Acme Inc")])[0]
    second = EntityResolver().resolve([ent(EntityType.COMPANY, "ACME")])[0]

    assert (
        first.entity_id
        == second.entity_id
        == entity_id_for(EntityType.COMPANY, "acme.com", "acme")
    )
    assert first.entity_id != entity_id_for(EntityType.COMPANY, "globex.com", "acme")


def test_repeated_features_are_deduplicated_per_website() -> None:
    features = [
        ent(EntityType.FEATURE, "Visual Builder"),
        ent(EntityType.FEATURE, "visual builder"),
        ent(EntityType.FEATURE, "Connectors"),
    ]

    resolved = EntityResolver().resolve(features)

    assert sorted(e.canonical_name.lower() for e in resolved) == [
        "connectors",
        "visual builder",
    ]


def test_custom_matcher_can_be_plugged_in() -> None:
    class Never:
        def similarity(self, a: str, b: str) -> float:
            return 0.0

    near = [
        ent(EntityType.PRODUCT, "Workflow Builder Pro"),
        ent(EntityType.PRODUCT, "Workflow Builder Pro2"),
    ]

    assert len(EntityResolver(Never()).resolve(near)) == 2


def claim(subject: str, kind: EntityType, scope="acme.com") -> ResearchClaim:
    return ResearchClaim(
        subject_entity=subject, subject_type=kind, scope_domain=scope, predicate="feature",
        object_value="x", claim_text="t", source_url="https://acme.com/", source_domain=scope,
        evidence_excerpt="x", extraction_method=ExtractionMethod.DETERMINISTIC,
    )  # fmt: skip


def test_claims_are_linked_to_resolved_entities() -> None:
    resolver = EntityResolver()
    entities = resolver.resolve(
        [
            ent(EntityType.COMPANY, "Acme"),
            ent(EntityType.PRODUCT, "Acme Flow"),
            ent(EntityType.COMPANY, "Globex", "globex.com"),
        ]
    )
    by_name = {e.canonical_name: e for e in entities}

    claims = [
        claim("Acme Flow", EntityType.PRODUCT),
        claim("acme", EntityType.COMPANY),
        claim("Acme Flow", EntityType.PRICING),  # pricing claims attach to their owner
        claim("Unknown Thing", EntityType.PRODUCT),  # falls back to the site's company
        claim("Acme", EntityType.COMPANY, scope="nowhere.org"),
    ]

    ids = [c.entity_id for c in resolver.link_claims(claims, entities)]
    assert ids[0] == by_name["Acme Flow"].entity_id
    assert ids[1] == by_name["Acme"].entity_id
    assert ids[2] == by_name["Acme Flow"].entity_id
    assert ids[3] == by_name["Acme"].entity_id
    assert ids[4] is None
