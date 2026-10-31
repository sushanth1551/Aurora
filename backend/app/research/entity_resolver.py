"""Entity resolution: merge duplicate entities conservatively, with reasons.

Deterministic rules only. Entities are merged when their normalized names are
identical (or near-identical for products/features/services) *within the same
website*; similar names from different companies are never merged, and
uncertain matches stay separate with a recorded reason. Original names are
preserved as aliases and every entity gets a stable id (UUIDv5 of type, scope
and normalized name) so re-running research yields the same identifiers.
An embedding-based matcher can be plugged in later via ``EntityMatcher``; no
vector store is needed here.
"""

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from app.research.domains import normalize_name, registrable_domain
from app.schemas.intelligence import (
    EntityType,
    ExtractedEntity,
    ResearchClaim,
    ResolvedEntity,
)

MERGE_THRESHOLD = 0.92
POSSIBLE_THRESHOLD = 0.8
_TECH_ALIASES = {
    "nodejs": "nodejs", "node": "nodejs", "js": "javascript",
    "gcp": "googlecloud", "k8s": "kubernetes", "postgres": "postgresql",
    "amazonwebservices": "aws", "msteams": "microsoftteams", "restapis": "restapi",
}  # fmt: skip
_SEGMENT_ALIASES = {
    "smbs": "smb", "smes": "smb", "smallandmediumbusinesses": "smb",
    "smallbusinesses": "smallbusiness", "enterprises": "enterprise",
    "startups": "startup",
}  # fmt: skip


class EntityMatcher(Protocol):
    """Pluggable similarity (e.g. embedding based). Returns 0..1."""

    def similarity(self, a: str, b: str) -> float: ...


class StringMatcher:
    """Default matcher: ``difflib`` ratio on normalized names."""

    def similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()


def normalize_entity_name(entity_type: EntityType, name: str) -> str:
    """Type-aware normalized form used for matching (original is preserved)."""
    if entity_type == EntityType.COMPANY:
        return normalize_name(name)
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    if entity_type == EntityType.TECHNOLOGY:
        return _TECH_ALIASES.get(compact, compact)
    if entity_type == EntityType.TARGET_CUSTOMER:
        return _SEGMENT_ALIASES.get(compact, compact)
    return compact


def _scope(entity_type: EntityType, scope_domain: str | None) -> str:
    if entity_type == EntityType.TECHNOLOGY or not scope_domain:
        return "*"  # technologies are shared concepts across websites
    return registrable_domain(scope_domain)


def entity_id_for(entity_type: EntityType, scope: str, normalized: str) -> UUID:
    """Stable identifier for an entity."""
    return uuid5(NAMESPACE_URL, f"aurora:{entity_type.value}:{scope}:{normalized}")


def _merge_value(
    target: dict[str, Any], key: str, value: Any, alternatives: dict[str, list[Any]]
) -> None:
    if value in (None, "", [], {}):
        return
    current = target.get(key)
    if current in (None, "", [], {}):
        target[key] = value
    elif isinstance(current, list) and isinstance(value, list):
        for item in value:
            if item not in current:
                current.append(item)
    elif current != value:
        alts = alternatives.setdefault(key, [])
        if value not in alts:
            alts.append(value)


class EntityResolver:
    """Resolve extracted entities into deduplicated, identified entities."""

    def __init__(self, matcher: EntityMatcher | None = None) -> None:
        self._matcher = matcher or StringMatcher()

    def resolve(self, entities: list[ExtractedEntity]) -> list[ResolvedEntity]:
        """Group by (type, scope, normalized name), then consider near-duplicates."""
        groups: dict[tuple[EntityType, str, str], list[ExtractedEntity]] = {}
        for entity in entities:
            norm = normalize_entity_name(entity.entity_type, entity.name)
            if not norm:
                continue
            key = (
                entity.entity_type,
                _scope(entity.entity_type, entity.scope_domain),
                norm,
            )
            groups.setdefault(key, []).append(entity)
        resolved = {key: self._build(key, items) for key, items in groups.items()}
        self._near_duplicates(resolved)
        return list(resolved.values())

    def link_claims(
        self, claims: list[ResearchClaim], entities: list[ResolvedEntity]
    ) -> list[ResearchClaim]:
        """Attach ``entity_id`` to claims whose subject matches a resolved entity."""
        index = {
            (e.entity_type, _scope(e.entity_type, e.scope_domain), e.normalized_name): e
            for e in entities
        }
        linked: list[ResearchClaim] = []
        for claim in claims:
            scope = _scope(EntityType.COMPANY, claim.scope_domain)
            norm_cache = {
                t: normalize_entity_name(t, claim.subject_entity)
                for t in (EntityType.COMPANY, EntityType.PRODUCT, EntityType.SERVICE)
            }
            order = [claim.subject_type] if claim.subject_type in norm_cache else []
            order += [
                t
                for t in (EntityType.PRODUCT, EntityType.SERVICE, EntityType.COMPANY)
                if t not in order
            ]
            target = next(
                (
                    index[(t, scope, norm_cache[t])]
                    for t in order
                    if (t, scope, norm_cache[t]) in index
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        e
                        for e in entities
                        if e.entity_type == EntityType.COMPANY
                        and _scope(e.entity_type, e.scope_domain) == scope
                    ),
                    None,
                )
            linked.append(
                claim.model_copy(
                    update={"entity_id": target.entity_id if target else None}
                )
            )
        return linked

    # -- internals ----------------------------------------------------------------

    def _build(
        self, key: tuple[EntityType, str, str], items: list[ExtractedEntity]
    ) -> ResolvedEntity:
        entity_type, scope, norm = key
        names = Counter(i.name for i in items)
        canonical = sorted(names, key=lambda n: (-names[n], len(n)))[0]
        attributes: dict[str, Any] = {}
        alternatives: dict[str, list[Any]] = {}
        sources: list[str] = []
        for item in items:
            for k, v in item.data.items():
                if k not in ("name", "source_urls"):
                    _merge_value(attributes, k, v, alternatives)
            sources.extend(u for u in item.source_urls if u not in sources)
        if alternatives:
            attributes["_alternatives"] = alternatives
        reasons: list[str] = []
        if len(items) > 1:
            reasons.append(
                f"merged {len(items)} mentions with identical normalized name '{norm}'"
                + (f" on {scope}" if scope != "*" else " across websites")
            )
        confidence = min(
            0.95, max(i.confidence for i in items) + 0.05 * (len(sources) - 1)
        )
        return ResolvedEntity(
            entity_id=entity_id_for(entity_type, scope, norm),
            entity_type=entity_type,
            canonical_name=canonical,
            normalized_name=norm,
            aliases=sorted(n for n in names if n != canonical),
            description=attributes.get("description"),
            attributes=attributes,
            source_urls=sources,
            scope_domain=None if scope == "*" else scope,
            confidence=round(confidence, 3),
            match_reasons=reasons,
            original_names=sorted(names),
        )

    def _near_duplicates(
        self, resolved: dict[tuple[EntityType, str, str], ResolvedEntity]
    ) -> None:
        """Merge near-identical names in one scope; note (don't merge) the uncertain."""
        fuzzy_types = (EntityType.PRODUCT, EntityType.FEATURE, EntityType.SERVICE)
        by_bucket: dict[tuple[EntityType, str], list[tuple[EntityType, str, str]]] = {}
        for key in resolved:
            by_bucket.setdefault((key[0], key[1]), []).append(key)
        for (entity_type, _scope_key), keys in by_bucket.items():
            if entity_type not in fuzzy_types or len(keys) < 2:
                continue
            keys.sort(key=lambda k: -len(k[2]))
            for i, left in enumerate(keys):
                for right in keys[i + 1 :]:
                    if left not in resolved or right not in resolved:
                        continue
                    score = self._matcher.similarity(left[2], right[2])
                    if score >= MERGE_THRESHOLD:
                        self._absorb(resolved, left, right, score)
                    elif score >= POSSIBLE_THRESHOLD:
                        resolved[right].match_reasons.append(
                            f"possible duplicate of '{resolved[left].canonical_name}' "
                            f"(similarity {score:.2f}); kept separate"
                        )
        self._note_cross_company(resolved)

    @staticmethod
    def _absorb(resolved: dict, keep: tuple, drop: tuple, score: float) -> None:
        main, other = resolved[keep], resolved.pop(drop)
        main.aliases = sorted(
            (set(main.aliases) | set(other.original_names) | {other.canonical_name})
            - {main.canonical_name}
        )
        main.original_names = sorted(
            set(main.original_names) | set(other.original_names)
        )
        main.source_urls += [u for u in other.source_urls if u not in main.source_urls]
        alternatives: dict[str, list[Any]] = main.attributes.pop("_alternatives", {})
        for k, v in other.attributes.items():
            if k == "_alternatives":
                continue
            _merge_value(main.attributes, k, v, alternatives)
        if alternatives:
            main.attributes["_alternatives"] = alternatives
        main.match_reasons.append(
            f"merged near-identical name '{other.canonical_name}' "
            f"(similarity {score:.2f}) within the same website"
        )
        main.confidence = round(min(main.confidence, 0.85), 3)

    def _note_cross_company(
        self, resolved: dict[tuple[EntityType, str, str], ResolvedEntity]
    ) -> None:
        """Record (never merge) similar product names that belong to different sites."""
        products = [e for e in resolved.values() if e.entity_type == EntityType.PRODUCT]
        for i, a in enumerate(products):
            for b in products[i + 1 :]:
                if a.scope_domain == b.scope_domain:
                    continue
                if (
                    self._matcher.similarity(a.normalized_name, b.normalized_name)
                    >= MERGE_THRESHOLD
                ):
                    note = "similar name on a different website; treated as a different entity"
                    for entity, other in ((a, b), (b, a)):
                        text = (
                            f"{note}: '{other.canonical_name}' ({other.scope_domain})"
                        )
                        if text not in entity.match_reasons:
                            entity.match_reasons.append(text)


def host_of(url: str) -> str:
    """Hostname of ``url`` (helper for callers building scope keys)."""
    return urlsplit(url).hostname or ""
