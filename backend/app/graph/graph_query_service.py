"""Read-only graph queries and deterministic analytics.

All queries are built from ``GraphRepository`` primitives (bounded, parameterized,
allowlisted), so they behave identically on Neo4j and the in-memory store.

Every related item reports ``verification_status``, ``assertion`` and
``verified`` (true only for supported / partially supported *direct* edges).
Inferred edges (technology mentions as ``USES``, industry-based
``COMPETES_WITH``) are returned but never marked verified.

Overlap measure used in comparisons (documented, not a "score" of quality):
``jaccard = |A ∩ B| / |A ∪ B|`` over the compared sets (shared technology ids,
integration ids, topic ids, or normalized feature names), with the counts
shown alongside so the ratio can be checked.
"""

from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import ResourceNotFoundException
from app.graph.graph_exceptions import GraphValidationError
from app.graph.graph_repository import Direction, GraphRepository
from app.graph.graph_schema import Label, Rel
from app.graph.models import VERIFIED_STATUSES, GraphEdge, GraphNode
from app.graph.provenance_manager import ProvenanceService

_NAME_KEYS = ("canonical_name", "title", "keyword", "url", "claim_text")


def node_view(node: GraphNode) -> dict[str, Any]:
    p = node.properties
    name = next((p[k] for k in _NAME_KEYS if p.get(k)), node.id)
    keep = {k: v for k, v in p.items() if k not in ("evidence_excerpt",)}
    return {"id": node.id, "label": node.label.value, "name": name, "properties": keep}


def edge_view(edge: GraphEdge, other: GraphNode, anchor: str) -> dict[str, Any]:
    p = edge.properties
    status = p.get("verification_status")
    assertion = p.get("assertion", "direct")
    return {
        "relationship": edge.type.value,
        "direction": "out" if edge.source_id == anchor else "in",
        "node": node_view(other),
        "verification_status": status,
        "assertion": assertion,
        "verified": status in VERIFIED_STATUSES and assertion == "direct",
        "has_conflict": bool(p.get("has_conflict")),
        "basis": p.get("basis"),
        "role": p.get("role"),
        "claim_count": len(p.get("claim_ids", [])),
        "source_count": len(p.get("source_ids", [])),
        "claim_ids": p.get("claim_ids", []),
        "confidence": p.get("confidence"),
        "first_observed_at": p.get("first_observed_at"),
        "last_observed_at": p.get("last_observed_at"),
    }


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return round(len(a & b) / len(union), 3) if union else 0.0


class GraphQueryService:
    def __init__(
        self, repository: GraphRepository, settings: Settings | None = None
    ) -> None:
        self._repo = repository
        self._s = settings or get_settings()
        self.provenance = ProvenanceService(
            repository, max_results=self._s.graph_max_query_results
        )

    # -- guards ---------------------------------------------------------------------------------

    def _limit(self, limit: int | None) -> int:
        cap = self._s.graph_max_query_results
        if limit is not None and limit < 1:
            raise GraphValidationError("limit must be >= 1")
        return min(limit or cap, cap)

    def _depth(self, depth: int) -> int:
        if not 1 <= depth <= self._s.graph_max_traversal_depth:
            raise GraphValidationError(
                f"depth must be between 1 and {self._s.graph_max_traversal_depth}"
            )
        return depth

    async def _require(self, node_id: str, *labels: Label) -> GraphNode:
        node = await self._repo.get_node(node_id)
        if node is None or (labels and node.label not in labels):
            want = "/".join(lab.value for lab in labels) or "entity"
            raise ResourceNotFoundException(
                f"{want} {node_id} was not found in the graph."
            )
        return node

    async def _related(self, node_id: str, direction: Direction, rels: list[Rel],
                       labels: list[Label] | None = None, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:  # fmt: skip
        edges = await self._repo.edges(
            node_id, direction=direction, rel_types=rels, neighbor_labels=labels,
            limit=self._limit(limit), offset=max(0, offset),
        )  # fmt: skip
        return [edge_view(e, n, node_id) for e, n in edges]

    # -- entity -----------------------------------------------------------------------------------

    async def entity(self, entity_id: str) -> dict[str, Any]:
        node = await self._require(entity_id)
        return {**node_view(node), "degree": await self._repo.degree(entity_id)}

    async def relationships(self, entity_id: str, *, direction: Direction = "both", types: list[str] | None = None,
                            labels: list[str] | None = None, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:  # fmt: skip
        await self._require(entity_id)
        try:
            rels = [Rel(t) for t in types] if types else [r for r in Rel]
            labs = [Label(lab) for lab in labels] if labels else None
        except ValueError as exc:
            raise GraphValidationError("unknown relationship type or label") from exc
        return await self._related(entity_id, direction, rels, labs, limit, offset)

    async def search(
        self,
        label: str,
        name: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            lab = Label(label)
        except ValueError as exc:
            raise GraphValidationError("unknown label") from exc
        from app.graph.normalization import normalize_domain, normalize_text

        nodes = await self._repo.find_nodes(
            lab,
            name=normalize_text(name) if name else None,
            domain=normalize_domain(domain),
            limit=self._limit(limit),
        )
        return [node_view(n) for n in nodes]

    # -- required queries ---------------------------------------------------------------------------

    async def company_profile(self, company_id: str) -> dict[str, Any]:
        node = await self._require(company_id, Label.COMPANY)
        return {
            "company": node_view(node),
            "products": await self.company_products(company_id),
            "services": await self.company_services(company_id),
            "technologies": await self._related(
                company_id, "out", [Rel.USES], [Label.TECHNOLOGY]
            ),
            "integrations": await self._related(
                company_id, "out", [Rel.INTEGRATES_WITH], [Label.INTEGRATION]
            ),
            "topics": await self._related(
                company_id, "out", [Rel.OPERATES_IN], [Label.TOPIC]
            ),
            "competitors": await self.competitors(company_id),
            "claim_count": len(await self.provenance.claims_for_entity(company_id)),
            "degree": await self._repo.degree(company_id),
        }

    async def company_products(
        self, company_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(company_id, Label.COMPANY)
        return await self._related(
            company_id, "out", [Rel.OFFERS], [Label.PRODUCT], limit
        )

    async def company_services(
        self, company_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(company_id, Label.COMPANY)
        return await self._related(
            company_id, "out", [Rel.PROVIDES], [Label.SERVICE], limit
        )

    async def product_technologies(
        self, product_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(product_id, Label.PRODUCT)
        return await self._related(
            product_id, "out", [Rel.USES], [Label.TECHNOLOGY], limit
        )

    async def product_features(
        self, product_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(product_id, Label.PRODUCT)
        return await self._related(
            product_id, "out", [Rel.HAS_FEATURE], [Label.FEATURE], limit
        )

    async def product_integrations(
        self, product_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(product_id, Label.PRODUCT)
        return await self._related(
            product_id, "out", [Rel.INTEGRATES_WITH], [Label.INTEGRATION], limit
        )

    async def competitors(
        self, entity_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        node = await self._require(entity_id, Label.COMPANY, Label.PRODUCT)
        return await self._related(
            entity_id, "both", [Rel.COMPETES_WITH], [node.label], limit
        )

    async def technology_companies(
        self, technology_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(technology_id, Label.TECHNOLOGY)
        direct = await self._related(
            technology_id, "in", [Rel.USES], [Label.COMPANY], limit
        )
        via_products = []
        for item in await self._related(
            technology_id, "in", [Rel.USES], [Label.PRODUCT], limit
        ):
            owner = item["node"]["properties"].get("company_id")
            if owner and (company := await self._repo.get_node(owner)):
                via_products.append(
                    {
                        **item,
                        "node": node_view(company),
                        "via_product": item["node"]["id"],
                    }
                )
        return (direct + via_products)[: self._limit(limit)]

    async def feature_products(
        self, feature_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(feature_id, Label.FEATURE)
        return await self._related(
            feature_id, "in", [Rel.HAS_FEATURE], [Label.PRODUCT], limit
        )

    async def article_topics(
        self, article_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(article_id, Label.ARTICLE)
        return await self._related(
            article_id, "out", [Rel.COVERS], [Label.TOPIC], limit
        )

    async def topic_articles(
        self, topic_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(topic_id, Label.TOPIC)
        return await self._related(topic_id, "in", [Rel.COVERS], [Label.ARTICLE], limit)

    async def claim_sources(self, claim_id: str) -> list[dict[str, Any]]:
        await self._require(claim_id, Label.CLAIM)
        return await self.provenance.sources_for_claim(claim_id)

    async def entity_claims(
        self, entity_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        await self._require(entity_id)
        return await self.provenance.claims_for_entity(
            entity_id, limit=self._limit(limit)
        )

    async def evidence(self, entity_id: str) -> dict[str, Any]:
        await self._require(entity_id)
        return await self.provenance.evidence_bundle(entity_id)

    async def explore(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        await self._require(entity_id)
        try:
            rels = [Rel(t) for t in types] if types else None
        except ValueError as exc:
            raise GraphValidationError("unknown relationship type") from exc
        nodes, edges = await self._repo.neighborhood(
            entity_id,
            depth=self._depth(depth),
            rel_types=rels,
            limit=self._limit(limit),
        )
        return {
            "root": entity_id,
            "depth": depth,
            "nodes": [node_view(n) for n in nodes],
            "edges": [
                {
                    "type": e.type.value,
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "verification_status": e.properties.get("verification_status"),
                    "assertion": e.properties.get("assertion", "direct"),
                    "role": e.properties.get("role"),
                }
                for e in edges
            ],  # fmt: skip
            "truncated": len(edges) >= self._limit(limit),
        }

    # -- analytics ------------------------------------------------------------------------------------

    async def _ids(
        self, node_id: str, rel: Rel, label: Label
    ) -> dict[str, dict[str, Any]]:
        items = await self._related(
            node_id, "out", [rel], [label], self._s.graph_max_query_results
        )
        return {i["node"]["id"]: i for i in items}

    async def _feature_names(self, product_ids: list[str]) -> dict[str, list[str]]:
        names: dict[str, list[str]] = {}
        for pid in product_ids:
            for item in await self._related(
                pid, "out", [Rel.HAS_FEATURE], [Label.FEATURE]
            ):
                key = (
                    item["node"]["properties"].get("normalized_name")
                    or item["node"]["name"]
                )
                names.setdefault(key, []).append(item["node"]["id"])
        return names

    @staticmethod
    def _overlap(name: str, a: dict, b: dict) -> dict[str, Any]:
        shared = sorted(set(a) & set(b))
        return {
            "dimension": name, "shared": shared, "only_a": sorted(set(a) - set(b)),
            "only_b": sorted(set(b) - set(a)), "jaccard": jaccard(set(a), set(b)),
            "counts": {"a": len(a), "b": len(b), "shared": len(shared)},
        }  # fmt: skip

    async def compare_companies(self, a: str, b: str) -> dict[str, Any]:
        na = await self._require(a, Label.COMPANY)
        nb = await self._require(b, Label.COMPANY)
        prods_a = await self._ids(a, Rel.OFFERS, Label.PRODUCT)
        prods_b = await self._ids(b, Rel.OFFERS, Label.PRODUCT)
        tech_a = await self._ids(a, Rel.USES, Label.TECHNOLOGY)
        tech_b = await self._ids(b, Rel.USES, Label.TECHNOLOGY)
        for pid in prods_a:
            tech_a |= await self._ids(pid, Rel.USES, Label.TECHNOLOGY)
        for pid in prods_b:
            tech_b |= await self._ids(pid, Rel.USES, Label.TECHNOLOGY)
        integ_a = await self._ids(a, Rel.INTEGRATES_WITH, Label.INTEGRATION)
        integ_b = await self._ids(b, Rel.INTEGRATES_WITH, Label.INTEGRATION)
        for pid in prods_a:
            integ_a |= await self._ids(pid, Rel.INTEGRATES_WITH, Label.INTEGRATION)
        for pid in prods_b:
            integ_b |= await self._ids(pid, Rel.INTEGRATES_WITH, Label.INTEGRATION)
        topics_a = await self._ids(a, Rel.OPERATES_IN, Label.TOPIC)
        topics_b = await self._ids(b, Rel.OPERATES_IN, Label.TOPIC)
        feats_a = await self._feature_names(list(prods_a))
        feats_b = await self._feature_names(list(prods_b))
        return {
            "a": node_view(na),
            "b": node_view(nb),
            "comparisons": [
                self._overlap(
                    "technologies (mentions count; mention is not proof of use)",
                    tech_a,
                    tech_b,
                ),
                self._overlap("integrations", integ_a, integ_b),
                self._overlap("topics", topics_a, topics_b),
                self._overlap(
                    "feature names (normalized; features are per-website nodes)",
                    feats_a,
                    feats_b,
                ),
            ],
            "products": {"a": sorted(prods_a), "b": sorted(prods_b)},
            "measure": "jaccard = |shared| / |union| over node ids (feature names for features)",
        }

    async def compare_products(self, a: str, b: str) -> dict[str, Any]:
        na = await self._require(a, Label.PRODUCT)
        nb = await self._require(b, Label.PRODUCT)
        return {
            "a": node_view(na),
            "b": node_view(nb),
            "comparisons": [
                self._overlap(
                    "feature names (normalized)",
                    await self._feature_names([a]),
                    await self._feature_names([b]),
                ),
                self._overlap(
                    "integrations",
                    await self._ids(a, Rel.INTEGRATES_WITH, Label.INTEGRATION),
                    await self._ids(b, Rel.INTEGRATES_WITH, Label.INTEGRATION),
                ),
                self._overlap(
                    "technologies",
                    await self._ids(a, Rel.USES, Label.TECHNOLOGY),
                    await self._ids(b, Rel.USES, Label.TECHNOLOGY),
                ),
            ],  # fmt: skip
            "measure": "jaccard = |shared| / |union|",
        }

    async def competitor_network(
        self, company_id: str, depth: int = 2, limit: int | None = None
    ) -> dict[str, Any]:
        await self._require(company_id, Label.COMPANY)
        return await self.explore(company_id, depth=min(depth, self._s.graph_max_traversal_depth),
                                  types=[Rel.COMPETES_WITH.value], limit=limit)  # fmt: skip

    async def technology_network(
        self, technology_id: str, depth: int = 2, limit: int | None = None
    ) -> dict[str, Any]:
        await self._require(technology_id, Label.TECHNOLOGY)
        return await self.explore(technology_id, depth=min(depth, self._s.graph_max_traversal_depth),
                                  types=[Rel.USES.value, Rel.RELATED_TO.value, Rel.OFFERS.value], limit=limit)  # fmt: skip

    async def product_network(
        self, product_id: str, depth: int = 2, limit: int | None = None
    ) -> dict[str, Any]:
        await self._require(product_id, Label.PRODUCT)
        return await self.explore(product_id, depth=min(depth, self._s.graph_max_traversal_depth),
                                  types=[Rel.RELATED_TO.value, Rel.COMPETES_WITH.value, Rel.OFFERS.value], limit=limit)  # fmt: skip
