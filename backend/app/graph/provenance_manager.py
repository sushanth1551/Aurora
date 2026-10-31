"""Evidence provenance: Source -SUPPORTS-> Claim -MENTIONS-> entity.

Building (write side): every stored claim becomes a ``Claim`` node linked to
its ``Source`` (``SUPPORTS``, with ``excerpt_verified``) and to the entities it
is about (``MENTIONS``, ``role`` = subject/object). Unsupported and unverified
claims are kept, with their status, so nothing is lost and nothing is upgraded.

Traversal (read side): helpers that walk the same directions consistently and
return claims with their verification status. A relationship is never treated
as verified because it exists; callers check ``verification_status``.
"""

from typing import Any

from app.graph.graph_schema import Label, Rel, check_relationship
from app.graph.models import (
    VERIFIED_STATUSES,
    ClaimNode,
    GraphEntity,
    GraphNode,
    RelationshipRow,
)

EXCERPT_CHECKED = frozenset({"supported", "partially_supported", "conflicting"})


def build_provenance(
    claims: list[ClaimNode],
    claim_links: dict[str, tuple[str | None, str | None]],
    nodes: dict[str, GraphEntity],
) -> tuple[list[RelationshipRow], int]:
    """SUPPORTS and MENTIONS rows plus the number of claims linked to an entity."""
    rows: list[RelationshipRow] = []
    linked = 0
    for c in claims:
        if c.source_node_id:
            rows.append(
                RelationshipRow(
                    type=Rel.SUPPORTS, source_id=c.source_node_id, source_label=Label.SOURCE,
                    target_id=c.id, target_label=Label.CLAIM, verification_status=c.verification_status,
                    extraction_method=c.extraction_method, observed_at=c.observed_at,
                    excerpt_verified=c.verification_status in EXCERPT_CHECKED,
                    claim_ids=[c.id], evidence_ids=[c.evidence_id],
                    source_record_ids=[c.source_record_id] if c.source_record_id else [],
                    research_job_id=c.research_job_id,
                )
            )  # fmt: skip
        subject_id, object_id = claim_links.get(c.id, (None, None))
        any_link = False
        for node_id, role in ((subject_id, "subject"), (object_id, "object")):
            node = nodes.get(node_id or "")
            if node is None or check_relationship(
                Rel.MENTIONS, Label.CLAIM, node.label
            ):
                continue
            any_link = True
            rows.append(
                RelationshipRow(
                    type=Rel.MENTIONS, source_id=c.id, source_label=Label.CLAIM, target_id=node.id,
                    target_label=node.label, role=role, verification_status=c.verification_status,
                    extraction_method=c.extraction_method, observed_at=c.observed_at,
                    claim_ids=[c.id], evidence_ids=[c.evidence_id], research_job_id=c.research_job_id,
                )
            )  # fmt: skip
        linked += any_link
    return rows, linked


def claim_view(node: GraphNode) -> dict[str, Any]:
    p = node.properties
    return {
        "claim_id": node.id,
        "evidence_id": p.get("evidence_id"),
        "predicate": p.get("predicate"),
        "subject": p.get("subject"),
        "object_value": p.get("object_value"),
        "claim_text": p.get("claim_text"),
        "evidence_excerpt": p.get("evidence_excerpt"),
        "verification_status": p.get("verification_status"),
        "verified": p.get("verification_status") in VERIFIED_STATUSES,
        "extraction_method": p.get("extraction_method"),
        "confidence": p.get("confidence"),
        "observed_at": p.get("observed_at"),
        "research_job_id": p.get("research_job_id"),
        "source_url": p.get("source_url"),
    }


def source_view(node: GraphNode) -> dict[str, Any]:
    p = node.properties
    return {
        "source_id": node.id,
        "url": p.get("url"),
        "domain": p.get("domain"),
        "title": p.get("title"),
        "source_type": p.get("source_type"),
        "content_hash": p.get("content_hash"),
        "retrieved_at": p.get("retrieved_at"),
        "source_record_ids": p.get("source_record_ids", []),
    }


class ProvenanceService:
    """Read-side provenance traversal over a ``GraphRepository``."""

    def __init__(self, repository: Any, *, max_results: int = 100) -> None:
        self._repo = repository
        self._max = max_results

    async def claims_for_entity(
        self, entity_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        edges = await self._repo.edges(
            entity_id, direction="in", rel_types=[Rel.MENTIONS], neighbor_labels=[Label.CLAIM],
            limit=min(limit or self._max, self._max),
        )  # fmt: skip
        out = []
        for edge, claim in edges:
            view = claim_view(claim)
            view["role"] = edge.properties.get("role")
            out.append(view)
        return out

    async def sources_for_claim(self, claim_id: str) -> list[dict[str, Any]]:
        edges = await self._repo.edges(
            claim_id,
            direction="in",
            rel_types=[Rel.SUPPORTS],
            neighbor_labels=[Label.SOURCE],
            limit=self._max,
        )
        out = []
        for edge, source in edges:
            view = source_view(source)
            view["excerpt_verified"] = edge.properties.get("excerpt_verified")
            out.append(view)
        return out

    async def sources_for_entity(
        self, entity_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for claim in await self.claims_for_entity(entity_id, limit=limit):
            for src in await self.sources_for_claim(claim["claim_id"]):
                entry = seen.setdefault(
                    src["source_id"], {**src, "claim_ids": [], "verified_claims": 0}
                )
                entry["claim_ids"].append(claim["claim_id"])
                entry["verified_claims"] += int(claim["verified"])
        return list(seen.values())[: limit or self._max]

    async def claims_for_relationship(
        self, rel_type: Rel, source_id: str, target_id: str
    ) -> list[dict[str, Any]]:
        edges = await self._repo.edges(
            source_id, direction="both", rel_types=[rel_type], limit=self._max
        )
        claim_ids: list[str] = []
        for edge, other in edges:
            if other.id == target_id:
                claim_ids = list(edge.properties.get("claim_ids", []))
                break
        nodes = await self._repo.get_nodes(claim_ids[: self._max])
        return [claim_view(n) for n in nodes if n.label is Label.CLAIM]

    async def conflicting_claims(self, entity_id: str) -> list[dict[str, Any]]:
        return [
            c
            for c in await self.claims_for_entity(entity_id)
            if c["verification_status"] == "conflicting"
        ]

    async def evidence_bundle(self, entity_id: str) -> dict[str, Any]:
        claims = await self.claims_for_entity(entity_id)
        sources = await self.sources_for_entity(entity_id)
        return {
            "entity_id": entity_id,
            "claims": claims,
            "sources": sources,
            "verified_claims": sum(c["verified"] for c in claims),
            "unverified_claims": sum(
                not c["verified"] and c["verification_status"] != "conflicting"
                for c in claims
            ),
            "conflicting_claims": [
                c for c in claims if c["verification_status"] == "conflicting"
            ],
        }
