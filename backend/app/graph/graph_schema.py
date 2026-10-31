"""Knowledge graph vocabulary: labels, relationship registry, constraints, indexes.

This module is the **only** source of label and relationship-type strings that
may appear in Cypher. Anything that reaches a query is checked against these
allowlists first; user input never becomes a label or type.

Every node carries its type label plus ``GraphNode`` (unique ``id``) so lookups
by id are indexed regardless of type. Uniqueness constraints and range indexes
work on Neo4j 5 Community; node-key / property-existence constraints are
Enterprise-only and are not used.
"""

from dataclasses import dataclass, field
from enum import StrEnum

SCHEMA_VERSION = "5.1.0"
BASE_LABEL = "GraphNode"


class Label(StrEnum):
    COMPANY = "Company"
    PRODUCT = "Product"
    SERVICE = "Service"
    TECHNOLOGY = "Technology"
    FEATURE = "Feature"
    INTEGRATION = "Integration"
    KEYWORD = "Keyword"
    TOPIC = "Topic"
    ARTICLE = "Article"
    SOURCE = "Source"
    CLAIM = "Claim"


ENTITY_LABELS = frozenset(
    {Label.COMPANY, Label.PRODUCT, Label.SERVICE, Label.TECHNOLOGY, Label.FEATURE,
     Label.INTEGRATION, Label.KEYWORD, Label.TOPIC, Label.ARTICLE}
)  # fmt: skip

#: Node properties per label (``id`` and timestamps are implied).
NODE_PROPERTIES: dict[Label, tuple[str, ...]] = {
    Label.COMPANY: ("canonical_name", "normalized_name", "official_domain", "website_url",
                    "description", "industry", "headquarters", "founded_year",
                    "verification_status", "source_record_id"),
    Label.PRODUCT: ("canonical_name", "normalized_name", "description", "category",
                    "product_url", "company_id", "verification_status", "source_record_id"),
    Label.SERVICE: ("canonical_name", "normalized_name", "description", "category",
                    "service_url", "verification_status", "source_record_id"),
    Label.TECHNOLOGY: ("canonical_name", "normalized_name", "category", "description",
                       "official_documentation_url", "verification_status"),
    Label.FEATURE: ("canonical_name", "normalized_name", "description", "category",
                    "verification_status"),
    Label.INTEGRATION: ("canonical_name", "normalized_name", "description",
                        "integration_url", "verification_status"),
    Label.KEYWORD: ("keyword", "normalized_keyword", "search_intent", "language"),
    Label.TOPIC: ("canonical_name", "normalized_name", "description", "category"),
    Label.ARTICLE: ("title", "canonical_url", "article_type", "publication_date", "content_hash"),
    Label.SOURCE: ("url", "normalized_url", "domain", "title", "source_type",
                   "credibility_metadata", "content_hash", "retrieved_at"),
    Label.CLAIM: ("subject", "predicate", "object_value", "claim_text", "verification_status",
                  "confidence", "extraction_method", "observed_at"),
}  # fmt: skip


class Rel(StrEnum):
    OFFERS = "OFFERS"
    PROVIDES = "PROVIDES"
    USES = "USES"
    HAS_FEATURE = "HAS_FEATURE"
    INTEGRATES_WITH = "INTEGRATES_WITH"
    COMPETES_WITH = "COMPETES_WITH"
    BELONGS_TO = "BELONGS_TO"
    TARGETS = "TARGETS"
    COVERS = "COVERS"
    LINKS_TO = "LINKS_TO"
    SUPPORTS = "SUPPORTS"
    MENTIONS = "MENTIONS"
    CITES = "CITES"
    RELATED_TO = "RELATED_TO"
    OPERATES_IN = "OPERATES_IN"


class Assertion(StrEnum):
    DIRECT = "direct"  # a claim states it
    INFERRED = "inferred"  # derived by a documented rule; never a confirmed fact


@dataclass(frozen=True)
class RelationshipSpec:
    """Registry entry: what a relationship may connect and how it is evidenced."""

    type: Rel
    sources: frozenset[Label]
    targets: frozenset[Label]
    evidence_required: bool = True
    inferable: bool = False
    symmetric: bool = False
    provenance: bool = True  # carries claim/source lists
    description: str = ""
    required_properties: tuple[str, ...] = field(default=())


_FACT_PROPS = ("verification_status", "assertion", "claim_ids", "observed_at")

L = Label
REGISTRY: dict[Rel, RelationshipSpec] = {
    spec.type: spec
    for spec in (
        RelationshipSpec(Rel.OFFERS, frozenset({L.COMPANY}), frozenset({L.PRODUCT}),
                         description="Company offers a product", required_properties=_FACT_PROPS),
        RelationshipSpec(Rel.PROVIDES, frozenset({L.COMPANY}), frozenset({L.SERVICE}),
                         description="Company provides a service", required_properties=_FACT_PROPS),
        # Module 3 records technologies as *mentions*; USES built from them is inferred.
        RelationshipSpec(Rel.USES, frozenset({L.PRODUCT, L.COMPANY}), frozenset({L.TECHNOLOGY}),
                         inferable=True, description="Product/company uses (or mentions) a technology",
                         required_properties=_FACT_PROPS),
        RelationshipSpec(Rel.HAS_FEATURE, frozenset({L.PRODUCT}), frozenset({L.FEATURE}),
                         description="Product has a documented feature", required_properties=_FACT_PROPS),
        RelationshipSpec(Rel.INTEGRATES_WITH, frozenset({L.PRODUCT, L.COMPANY}), frozenset({L.INTEGRATION}),
                         description="Product/company documents an integration", required_properties=_FACT_PROPS),
        RelationshipSpec(Rel.COMPETES_WITH, frozenset({L.COMPANY, L.PRODUCT}), frozenset({L.COMPANY, L.PRODUCT}),
                         inferable=True, symmetric=True,
                         description="Competition; basis recorded, inferred unless documented",
                         required_properties=(*_FACT_PROPS, "basis")),
        RelationshipSpec(Rel.BELONGS_TO, frozenset({L.KEYWORD}), frozenset({L.TOPIC}), inferable=True,
                         description="Keyword belongs to a topic (Module 6 producer)"),
        RelationshipSpec(Rel.TARGETS, frozenset({L.ARTICLE}), frozenset({L.KEYWORD}), evidence_required=False,
                         provenance=False, description="Article targets a keyword (Module 7 producer)"),
        RelationshipSpec(Rel.COVERS, frozenset({L.ARTICLE}), frozenset({L.TOPIC}), evidence_required=False,
                         provenance=False, description="Article covers a topic (Module 7 producer)"),
        RelationshipSpec(Rel.LINKS_TO, frozenset({L.ARTICLE}), frozenset({L.ARTICLE}), evidence_required=False,
                         provenance=False, description="Article links to another article"),
        RelationshipSpec(Rel.SUPPORTS, frozenset({L.SOURCE}), frozenset({L.CLAIM}), provenance=False,
                         description="Source asserts a claim (see excerpt_verified)"),
        RelationshipSpec(Rel.MENTIONS, frozenset({L.CLAIM}),
                         frozenset({L.COMPANY, L.PRODUCT, L.SERVICE, L.TECHNOLOGY, L.FEATURE,
                                    L.INTEGRATION, L.TOPIC}),
                         provenance=False, description="Claim is about an entity (role: subject/object)"),
        RelationshipSpec(Rel.CITES, frozenset({L.ARTICLE}), frozenset({L.SOURCE}), evidence_required=False,
                         provenance=False, description="Article cites a source"),
        RelationshipSpec(Rel.RELATED_TO, frozenset({L.PRODUCT, L.TECHNOLOGY}), frozenset({L.PRODUCT, L.TECHNOLOGY}),
                         inferable=True, symmetric=True, description="Same-type relatedness (documented basis)"),
        RelationshipSpec(Rel.OPERATES_IN, frozenset({L.COMPANY}), frozenset({L.TOPIC}),
                         description="Company operates in an industry/topic", required_properties=_FACT_PROPS),
    )
}  # fmt: skip

#: (source label, rel, target label) combinations that also need same-label symmetry.
SAME_LABEL_ONLY = frozenset({Rel.COMPETES_WITH, Rel.RELATED_TO})


def check_relationship(
    rel: Rel | str, source: Label | str, target: Label | str
) -> str | None:
    """Return a rejection reason, or ``None`` if the triple is allowed."""
    try:
        rel = Rel(rel)
        source, target = Label(source), Label(target)
    except ValueError:
        return "unknown label or relationship type"
    spec = REGISTRY[rel]
    if source not in spec.sources:
        return f"{source} cannot be the source of {rel}"
    if target not in spec.targets:
        return f"{target} cannot be the target of {rel}"
    if rel in SAME_LABEL_ONLY and source != target:
        return f"{rel} must connect nodes of the same type"
    return None


def label(value: str) -> Label:
    """Validate a label name coming from outside (raises ``ValueError``)."""
    return Label(value)


def rel_types(values: list[str] | None) -> list[Rel]:
    """Validate a list of relationship-type names (raises ``ValueError``)."""
    return [Rel(v) for v in values or []]


# -- constraints and indexes -----------------------------------------------------------


def _constraints() -> list[tuple[str, str]]:
    out = [
        (
            "graphnode_id_unique",
            f"CREATE CONSTRAINT graphnode_id_unique IF NOT EXISTS FOR (n:{BASE_LABEL}) REQUIRE n.id IS UNIQUE",
        )
    ]
    for lab in Label:
        name = f"{lab.value.lower()}_id_unique"
        out.append(
            (
                name,
                f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{lab.value}) REQUIRE n.id IS UNIQUE",
            )
        )
    out.append(
        (
            "schema_version_id_unique",
            "CREATE CONSTRAINT schema_version_id_unique IF NOT EXISTS FOR (n:GraphSchemaVersion) REQUIRE n.id IS UNIQUE",
        )
    )
    return out


_INDEXED: list[tuple[Label, str]] = [
    *[(lab, "canonical_name") for lab in (L.COMPANY, L.PRODUCT, L.SERVICE, L.TECHNOLOGY, L.FEATURE, L.INTEGRATION, L.TOPIC)],
    *[(lab, "normalized_name") for lab in (L.COMPANY, L.PRODUCT, L.SERVICE, L.TECHNOLOGY, L.FEATURE, L.INTEGRATION, L.TOPIC)],
    (L.COMPANY, "official_domain"), (L.COMPANY, "verification_status"), (L.PRODUCT, "verification_status"),
    (L.SOURCE, "url"), (L.SOURCE, "normalized_url"), (L.SOURCE, "domain"),
    (L.ARTICLE, "canonical_url"), (L.KEYWORD, "normalized_keyword"),
    (L.CLAIM, "verification_status"), (L.CLAIM, "research_job_id"), (L.CLAIM, "predicate"),
]  # fmt: skip


def _indexes() -> list[tuple[str, str]]:
    out = []
    for lab, prop in _INDEXED:
        name = f"{lab.value.lower()}_{prop}_idx"
        out.append(
            (
                name,
                f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{lab.value}) ON (n.{prop})",
            )
        )
    return out


CONSTRAINTS: list[tuple[str, str]] = _constraints()
INDEXES: list[tuple[str, str]] = _indexes()
