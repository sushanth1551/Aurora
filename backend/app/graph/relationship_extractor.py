"""Deterministic relationship extraction from stored claims, with validation.

Each claim maps to at most one *fact* relationship through ``PREDICATE_RULES``.
Rules:

* The (source label, type, target label) triple must pass the registry
  (``graph_schema.check_relationship``); otherwise the relationship is rejected
  with a reason and the claim is still kept as provenance.
* Claims whose excerpt was not found in the stored page (``unsupported``) never
  create fact relationships.
* ``mentions_technology`` becomes ``USES`` marked ``inferred``: a page mentioning
  a technology does not prove the product uses it.
* Several claims asserting the same edge are merged into one row whose
  ``claim_ids`` / ``source_ids`` keep every supporting claim and source.
* ``COMPETES_WITH`` is only *inferred* here (two companies in the same job whose
  verified industry claims name the same topic). Its ``basis`` records why; it
  is never presented as documented competition.

No LLM is used. ``RelationshipProposal`` is the extension point for a future
structured LLM extractor; proposals pass through the same validation and must
cite existing claims.
"""

from collections import defaultdict
from itertools import combinations

from pydantic import BaseModel, Field

from app.graph.graph_schema import REGISTRY, Assertion, Label, Rel, check_relationship
from app.graph.models import (
    STATUS_RANK,
    VERIFIED_STATUSES,
    ClaimNode,
    GraphEntity,
    RejectedRelationship,
    RelationshipRow,
    stronger,
)

#: predicate -> (relationship type, assertion, basis)
PREDICATE_RULES: dict[str, tuple[Rel, Assertion, str | None]] = {
    "offers_product": (Rel.OFFERS, Assertion.DIRECT, None),
    "offers_service": (Rel.PROVIDES, Assertion.DIRECT, None),
    "feature": (Rel.HAS_FEATURE, Assertion.DIRECT, None),
    "integration": (Rel.INTEGRATES_WITH, Assertion.DIRECT, None),
    "mentions_technology": (
        Rel.USES,
        Assertion.INFERRED,
        "technology mentioned on the subject's page",
    ),
    "industry": (Rel.OPERATES_IN, Assertion.DIRECT, None),
}
INFERRED_COMPETITOR_CONFIDENCE = 0.3


class RelationshipProposal(BaseModel):
    """A relationship proposed by any extractor (deterministic or LLM)."""

    type: Rel
    source_id: str
    target_id: str
    claim_ids: list[str] = Field(default_factory=list)
    assertion: Assertion = Assertion.INFERRED
    basis: str | None = None


class RelationshipExtractor:
    def extract(
        self,
        claims: list[ClaimNode],
        claim_links: dict[str, tuple[str | None, str | None]],
        nodes: dict[str, GraphEntity],
    ) -> tuple[list[RelationshipRow], list[RejectedRelationship]]:
        rows: list[RelationshipRow] = []
        rejected: list[RejectedRelationship] = []
        for claim in claims:
            rule = PREDICATE_RULES.get(claim.predicate)
            if rule is None:
                continue  # attribute / marketing predicates stay as claims
            rel, assertion, basis = rule
            subject_id, object_id = claim_links.get(claim.id, (None, None))
            src, dst = nodes.get(subject_id or ""), nodes.get(object_id or "")
            if src is None or dst is None:
                rejected.append(
                    RejectedRelationship(
                        type=rel, source=subject_id or "?", target=object_id or "?",
                        reason="subject or object entity is missing", claim_id=claim.id,
                    )
                )  # fmt: skip
                continue
            if claim.verification_status == "unsupported":
                rejected.append(
                    RejectedRelationship(
                        type=rel, source=src.id, target=dst.id,
                        reason="claim excerpt not found in its source; no fact edge created",
                        claim_id=claim.id,
                    )
                )  # fmt: skip
                continue
            reason = check_relationship(rel, src.label, dst.label)
            if reason:
                rejected.append(
                    RejectedRelationship(
                        type=rel,
                        source=src.id,
                        target=dst.id,
                        reason=reason,
                        claim_id=claim.id,
                    )
                )
                continue
            rows.append(self._row(rel, src, dst, claim, assertion, basis))
        rows += self._infer_competitors(rows, nodes)
        return self.merge(rows), rejected

    @staticmethod
    def _row(rel, src, dst, claim: ClaimNode, assertion, basis) -> RelationshipRow:
        return RelationshipRow(
            type=rel,
            source_id=src.id,
            source_label=src.label,
            target_id=dst.id,
            target_label=dst.label,
            assertion=assertion,
            verification_status=claim.verification_status,
            confidence=claim.confidence,
            extraction_method=claim.extraction_method,
            observed_at=claim.observed_at,
            claim_ids=[claim.id],
            evidence_ids=[claim.evidence_id],
            source_ids=[claim.source_node_id] if claim.source_node_id else [],
            source_record_ids=[claim.source_record_id]
            if claim.source_record_id
            else [],
            basis=basis,
            research_job_id=claim.research_job_id,
        )

    @staticmethod
    def _infer_competitors(
        rows: list[RelationshipRow], nodes: dict[str, GraphEntity]
    ) -> list[RelationshipRow]:
        by_topic: dict[str, list[RelationshipRow]] = defaultdict(list)
        for r in rows:
            if r.type is Rel.OPERATES_IN and r.verification_status in VERIFIED_STATUSES:
                by_topic[r.target_id].append(r)
        out: list[RelationshipRow] = []
        for topic_id, group in by_topic.items():
            companies = {r.source_id: r for r in group}
            topic = nodes.get(topic_id)
            for a, b in combinations(sorted(companies), 2):
                ra, rb = companies[a], companies[b]
                out.append(
                    RelationshipRow(
                        type=Rel.COMPETES_WITH,
                        source_id=a,
                        source_label=Label.COMPANY,
                        target_id=b,
                        target_label=Label.COMPANY,
                        assertion=Assertion.INFERRED,
                        verification_status="inferred",
                        confidence=INFERRED_COMPETITOR_CONFIDENCE,
                        extraction_method="deterministic_inference",
                        observed_at=max(
                            x for x in (ra.observed_at, rb.observed_at) if x
                        ),
                        claim_ids=[*ra.claim_ids, *rb.claim_ids],
                        evidence_ids=[*ra.evidence_ids, *rb.evidence_ids],
                        source_ids=list(
                            dict.fromkeys([*ra.source_ids, *rb.source_ids])
                        ),
                        basis=f"same_industry:{topic.canonical_name if topic else topic_id}",
                        research_job_id=ra.research_job_id,
                    )
                )
        return out

    def validate_proposals(
        self,
        proposals: list[RelationshipProposal],
        nodes: dict[str, GraphEntity],
        claims: dict[str, ClaimNode],
    ) -> tuple[list[RelationshipRow], list[RejectedRelationship]]:
        """Gate for future (e.g. LLM) proposals: same rules, and every cited
        claim must exist; uncited proposals for evidence-required types fail."""
        rows, rejected = [], []
        for p in proposals:
            src, dst = nodes.get(p.source_id), nodes.get(p.target_id)
            cited = [claims[c] for c in p.claim_ids if c in claims]
            reason = None
            if src is None or dst is None:
                reason = "unknown source or target node"
            else:
                reason = check_relationship(p.type, src.label, dst.label)
            spec = REGISTRY[p.type]
            if reason is None and spec.evidence_required and not cited:
                reason = "no existing claim supports the proposal"
            if (
                reason is None
                and p.assertion is Assertion.INFERRED
                and not spec.inferable
            ):
                reason = f"{p.type} cannot be inferred"
            if reason:
                rejected.append(
                    RejectedRelationship(
                        type=p.type,
                        source=p.source_id,
                        target=p.target_id,
                        reason=reason,
                    )
                )
                continue
            assert src is not None and dst is not None
            best = (
                max(cited, key=lambda c: STATUS_RANK.get(c.verification_status, 0))
                if cited
                else None
            )
            rows.append(
                RelationshipRow(
                    type=p.type, source_id=src.id, source_label=src.label, target_id=dst.id,
                    target_label=dst.label, assertion=p.assertion,
                    verification_status="inferred" if p.assertion is Assertion.INFERRED else (best.verification_status if best else "unverified"),
                    extraction_method="llm_proposal", observed_at=best.observed_at if best else None,
                    claim_ids=[c.id for c in cited], evidence_ids=[c.evidence_id for c in cited],
                    source_ids=[c.source_node_id for c in cited if c.source_node_id], basis=p.basis,
                )
            )  # fmt: skip
        return self.merge(rows), rejected

    @staticmethod
    def merge(rows: list[RelationshipRow]) -> list[RelationshipRow]:
        """Collapse rows for the same edge; keep every claim and source."""
        merged: dict[tuple, RelationshipRow] = {}
        for r in rows:
            src, dst = r.source_id, r.target_id
            if REGISTRY[r.type].symmetric and dst < src:
                r = r.model_copy(update={"source_id": dst, "target_id": src,
                                         "source_label": r.target_label, "target_label": r.source_label})  # fmt: skip
            key = (r.type, r.source_id, r.target_id, r.role)
            cur = merged.get(key)
            if cur is None:
                merged[key] = r.model_copy(deep=True)
                continue
            for attr in (
                "claim_ids",
                "evidence_ids",
                "source_ids",
                "source_record_ids",
            ):
                values = getattr(cur, attr)
                values.extend(v for v in getattr(r, attr) if v not in values)
            cur.verification_status = stronger(
                cur.verification_status, r.verification_status
            )
            if r.assertion is Assertion.DIRECT:
                cur.assertion = Assertion.DIRECT
            if r.confidence is not None and (
                cur.confidence is None or r.confidence > cur.confidence
            ):
                cur.confidence = r.confidence
            if r.observed_at and (
                cur.observed_at is None or r.observed_at > cur.observed_at
            ):
                cur.observed_at = r.observed_at
        return list(merged.values())
