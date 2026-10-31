"""Turn stored Module 2/3 research into graph entities, sources and claims.

Input is what PostgreSQL already holds (``research_entities``,
``research_claims``, ``research_sources``, ``research_websites``); nothing is
scraped or re-extracted. Entities named only as the *object* of a claim (the
product in ``offers_product``, a feature, an integration, a technology, an
industry) become graph entities derived from that claim, with the same
deterministic id formula Module 3 uses, so they merge with Module 3 entities
when both exist.

Company properties (headquarters, founded year, industry, description) are
copied only from supported or partially supported, non-conflicting claims;
conflicting values stay on the claims and the property is listed in
``conflicting_properties`` instead of picking a winner.
"""

import json
from dataclasses import dataclass, field

from app.graph.graph_schema import Label
from app.graph.models import (
    VERIFIED_STATUSES,
    ClaimNode,
    GraphEntity,
    SourceNode,
    stronger,
)
from app.graph.normalization import (
    concept_id,
    normalize_domain,
    normalize_text,
    normalize_url,
    source_id_for_url,
)
from app.intelligence.context_builder import score_source
from app.research.entity_resolver import entity_id_for, normalize_entity_name
from app.schemas.intelligence import (
    EntityType,
    OfficialStatus,
    ResearchClaim,
    ResolvedEntity,
    SourceKind,
    StoredWebsite,
)
from app.schemas.research import ExtractionStatus, ResearchSource

TYPE_TO_LABEL = {
    EntityType.COMPANY: Label.COMPANY,
    EntityType.PRODUCT: Label.PRODUCT,
    EntityType.SERVICE: Label.SERVICE,
    EntityType.TECHNOLOGY: Label.TECHNOLOGY,
    EntityType.FEATURE: Label.FEATURE,
}
#: predicate -> (object label, how the object id is formed)
OBJECT_PREDICATES: dict[str, tuple[Label, str]] = {
    "offers_product": (Label.PRODUCT, "scoped"),
    "offers_service": (Label.SERVICE, "scoped"),
    "feature": (Label.FEATURE, "scoped"),
    "integration": (Label.INTEGRATION, "concept"),
    "mentions_technology": (Label.TECHNOLOGY, "global"),
    "industry": (Label.TOPIC, "concept"),
}
_LABEL_TYPE = {
    Label.PRODUCT: EntityType.PRODUCT,
    Label.SERVICE: EntityType.SERVICE,
    Label.FEATURE: EntityType.FEATURE,
    Label.TECHNOLOGY: EntityType.TECHNOLOGY,
}
COMPANY_FACTS = ("description", "headquarters", "founded_year", "industry")


@dataclass
class Extraction:
    entities: dict[str, GraphEntity] = field(default_factory=dict)
    sources: dict[str, SourceNode] = field(default_factory=dict)
    claims: list[ClaimNode] = field(default_factory=list)
    #: claim id -> (subject entity id, object entity id | None)
    claim_links: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    verified_domains: set[str] = field(default_factory=set)


def object_entity_id(
    label: Label, mode: str, scope: str | None, name: str
) -> tuple[str, str]:
    """(id, match_key) for an entity named as a claim object."""
    if mode == "concept":
        key = normalize_entity_name(EntityType.FEATURE, name)  # compact alnum form
        return concept_id(label.value.lower(), key), key
    etype = _LABEL_TYPE[label]
    key = normalize_entity_name(etype, name)
    scope_key = (
        "*" if mode == "global" or not scope else normalize_domain(scope) or scope
    )
    return str(entity_id_for(etype, scope_key, key)), key


class EntityExtractor:
    """Build graph-ready records for one research job."""

    def extract(
        self,
        job_id: str,
        websites: list[StoredWebsite],
        entities: list[ResolvedEntity],
        claims: list[ResearchClaim],
        sources: list[ResearchSource],
    ) -> Extraction:
        out = Extraction()
        official = {
            normalize_domain(w.candidate.domain): w
            for w in websites
            if w.candidate.official_status is OfficialStatus.VERIFIED
        }
        out.verified_domains = {d for d in official if d}
        third_party = {
            normalize_domain(w.candidate.domain)
            for w in websites
            if w.candidate.official_status is OfficialStatus.THIRD_PARTY
        }
        for entity in entities:
            graph_entity = self._from_resolved(job_id, entity, official)
            if graph_entity is not None:
                out.entities[graph_entity.id] = graph_entity
        self._sources(job_id, sources, claims, official, third_party, out)
        self._claims(job_id, claims, out)
        self._company_facts(claims, out)
        return out

    # -- entities -----------------------------------------------------------------------

    @staticmethod
    def _from_resolved(
        job_id: str, e: ResolvedEntity, official: dict[str | None, StoredWebsite]
    ) -> GraphEntity | None:
        label = TYPE_TO_LABEL.get(e.entity_type)
        if label is None:  # pricing / target_customer stay as claims only
            return None
        scope = normalize_domain(e.scope_domain)
        props: dict = {"description": e.description} if e.description else {}
        if label is Label.COMPANY and scope in official:
            site = official[scope]
            props.update(official_domain=scope, website_url=site.candidate.homepage_url)
        names = list(dict.fromkeys([e.canonical_name, *e.original_names, *e.aliases]))
        return GraphEntity(
            id=str(e.entity_id),
            label=label,
            canonical_name=e.canonical_name,
            original_names=names,
            normalized_name=normalize_text(e.canonical_name),
            match_key=e.normalized_name,
            aliases=sorted({a for a in e.aliases if a != e.canonical_name}),
            scope_domain=scope,
            source_record_ids=[str(e.entity_id)],
            source_urls=list(e.source_urls),
            research_job_ids=[job_id],
            confidence=e.confidence,
            properties=props,
        )

    def _object_entity(
        self, out: Extraction, job_id: str, claim: ResearchClaim
    ) -> str | None:
        spec = OBJECT_PREDICATES.get(claim.predicate)
        name = (claim.object_value or "").strip()
        if spec is None or not name:
            return None
        label, mode = spec
        scope = normalize_domain(claim.scope_domain)
        node_id, key = object_entity_id(label, mode, scope, name)
        if not key:
            return None
        existing = out.entities.get(node_id)
        if existing is None:
            out.entities[node_id] = GraphEntity(
                id=node_id,
                label=label,
                canonical_name=name,
                original_names=[name],
                normalized_name=normalize_text(name),
                match_key=key,
                scope_domain=None if mode in ("global", "concept") else scope,
                source_urls=[claim.source_url],
                research_job_ids=[job_id],
                confidence=claim.confidence_score,
                verification_status=claim.verification_status.value,
                derived_from_claim=True,
            )
        else:
            if name not in existing.original_names:
                existing.original_names.append(name)
                if name != existing.canonical_name and name not in existing.aliases:
                    existing.aliases.append(name)
            if claim.source_url not in existing.source_urls:
                existing.source_urls.append(claim.source_url)
        return node_id

    # -- sources and claims -----------------------------------------------------------------

    @staticmethod
    def _sources(
        job_id: str,
        sources: list[ResearchSource],
        claims: list[ResearchClaim],
        official: dict,
        third_party: set,
        out: Extraction,
    ) -> None:
        cited = {normalize_url(c.source_url) for c in claims}
        for s in sources:
            url = s.final_url or s.normalized_url or s.original_url
            key = normalize_url(url)
            if (
                s.extraction_status is not ExtractionStatus.SUCCEEDED
                and key not in cited
            ):
                continue
            node_id = source_id_for_url(url)
            domain = normalize_domain(s.domain or url) or ""
            kind = (
                SourceKind.OFFICIAL
                if domain in official
                else SourceKind.THIRD_PARTY
                if domain in third_party
                else SourceKind.UNKNOWN
            )
            cred = score_source(
                url=url,
                domain=domain,
                kind=kind,
                snippet_only=s.extraction_status is not ExtractionStatus.SUCCEEDED,
            )
            node = out.sources.get(node_id)
            if node is None:
                out.sources[node_id] = SourceNode(
                    id=node_id,
                    url=url,
                    normalized_url=key,
                    domain=domain,
                    title=s.title,
                    source_type=kind.value,
                    credibility_metadata=json.dumps(
                        cred.model_dump(mode="json"), sort_keys=True
                    ),
                    content_hash=s.content_hash,
                    retrieved_at=s.extracted_at,
                    source_record_ids=[str(s.id)],
                    research_job_ids=[job_id],
                )
            elif str(s.id) not in node.source_record_ids:
                node.source_record_ids.append(str(s.id))
        # claims whose page row is missing still need a Source to hang off
        for c in claims:
            node_id = source_id_for_url(c.source_url)
            if node_id not in out.sources:
                domain = normalize_domain(c.source_domain) or c.source_domain
                out.sources[node_id] = SourceNode(
                    id=node_id,
                    url=c.source_url,
                    normalized_url=normalize_url(c.source_url),
                    domain=domain,
                    title=c.source_title,
                    source_type=c.source_kind.value,
                    content_hash=c.source_content_hash,
                    research_job_ids=[job_id],
                )

    def _claims(
        self, job_id: str, claims: list[ResearchClaim], out: Extraction
    ) -> None:
        url_to_record = {
            normalize_url(s.url): s.source_record_ids[0]
            for s in out.sources.values()
            if s.source_record_ids
        }
        for c in claims:
            subject_id = str(c.entity_id) if c.entity_id else None
            if subject_id and subject_id in out.entities:
                ent = out.entities[subject_id]
                ent.verification_status = stronger(
                    ent.verification_status, c.verification_status.value
                )
            object_id = self._object_entity(out, job_id, c)
            out.claims.append(
                ClaimNode(
                    id=str(c.claim_id),
                    evidence_id=f"c-{c.claim_id.hex[:10]}",
                    research_job_id=job_id,
                    subject=c.subject_entity,
                    predicate=c.predicate,
                    object_value=c.object_value,
                    claim_text=c.claim_text,
                    evidence_excerpt=c.evidence_excerpt[:1000],
                    source_url=c.source_url,
                    source_node_id=source_id_for_url(c.source_url),
                    source_record_id=url_to_record.get(normalize_url(c.source_url)),
                    verification_status=c.verification_status.value,
                    confidence=c.confidence_score,
                    extraction_method=c.extraction_method.value,
                    observed_at=c.observed_at,
                    notes=list(c.notes),
                )
            )
            out.claim_links[str(c.claim_id)] = (subject_id, object_id)

    @staticmethod
    def _company_facts(claims: list[ResearchClaim], out: Extraction) -> None:
        by_entity: dict[str, dict[str, set[str]]] = {}
        for c in claims:
            if c.predicate not in COMPANY_FACTS or not c.entity_id:
                continue
            if c.verification_status.value not in VERIFIED_STATUSES | {"conflicting"}:
                continue
            values = by_entity.setdefault(str(c.entity_id), {}).setdefault(
                c.predicate, set()
            )
            values.add(
                "\x00CONFLICT"
                if c.verification_status.value == "conflicting"
                else c.object_value
            )
        for entity_id, facts in by_entity.items():
            ent = out.entities.get(entity_id)
            if ent is None or ent.label is not Label.COMPANY:
                continue
            conflicts = []
            for predicate, values in facts.items():
                if "\x00CONFLICT" in values or len(values) > 1:
                    conflicts.append(predicate)
                    continue
                value = next(iter(values))
                if predicate == "description" and ent.properties.get("description"):
                    continue
                ent.properties[predicate] = (
                    int(value)
                    if predicate == "founded_year" and value.isdigit()
                    else value
                )
            if conflicts:
                ent.properties["conflicting_properties"] = sorted(conflicts)
