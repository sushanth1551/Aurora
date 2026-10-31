"""Build a ``UnifiedResearchContext`` from stored Module 2 and Module 3 data.

Nothing is fetched again: data comes from the repositories. Selection and
ranking are deterministic (no LLM). Source credibility is explainable and kept
separate from whether any claim is true; freshness and directness are separate
ranking inputs.

Rank score = 0.50 relevance + 0.25 credibility + 0.10 freshness + 0.15 directness.
"""

import re
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.exceptions import ResourceNotFoundException
from app.core.hashing import content_hash
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.intelligence.models import (
    Contradiction,
    EvidenceItem,
    EvidenceKind,
    InformationGap,
    SourceCredibility,
    SourceInfo,
    Tier,
    UnifiedResearchContext,
)
from app.intelligence.text import estimate_tokens, overlap, terms
from app.research.domains import registrable_domain
from app.schemas.intelligence import (
    EntityType,
    OfficialStatus,
    ResearchClaim,
    SourceKind,
    VerificationStatus,
)
from app.schemas.research import ExtractionStatus, ResearchSource, utc_now

SINGLE_VALUED = frozenset({"headquarters", "founded_year", "company_size"})
EXCERPT_CHARS = 500
MAX_ENTITY_LIST = 50
_DIRECTORIES = frozenset(
    {"g2.com", "capterra.com", "crunchbase.com", "linkedin.com", "clutch.co",
     "trustpilot.com", "glassdoor.com", "softwareadvice.com", "getapp.com",
     "producthunt.com", "goodfirms.co", "ambitionbox.com"}
)  # fmt: skip
_GOV_SUFFIXES = (".gov", ".gov.in", ".nic.in", ".gouv.fr", ".gov.uk")
_DOC_HINTS = ("/docs", "/documentation", "/developer", "/api", "/help", "/support")
_VERIFIED = {VerificationStatus.SUPPORTED, VerificationStatus.PARTIALLY_SUPPORTED}
_DIRECTNESS = {
    VerificationStatus.SUPPORTED: 1.0,
    VerificationStatus.PARTIALLY_SUPPORTED: 0.7,
    VerificationStatus.CONFLICTING: 0.5,
    VerificationStatus.UNVERIFIED: 0.4,
    VerificationStatus.UNSUPPORTED: 0.1,
}
#: (predicate, description, critical) required for every company.
REQUIRED_FACTS = (
    ("headquarters", "headquarters", False),
    ("offers_product", "products or services", True),
    ("price", "pricing", True),
    ("target_customer", "target customers", False),
)


class EvidenceRetriever(Protocol):
    """Relevance scorer. A vector-based retriever can implement this later."""

    def score(self, objective: str, items: list[EvidenceItem]) -> dict[str, float]: ...


class KeywordRetriever:
    """Deterministic term-overlap relevance (0..1)."""

    def score(self, objective: str, items: list[EvidenceItem]) -> dict[str, float]:
        wanted = terms(objective)
        scores: dict[str, float] = {}
        for item in items:
            haystack = terms(
                f"{item.entity or ''} {item.predicate or ''} {item.text} "
                f"{item.source_title or ''} {item.source_url}"
            )
            base = overlap(wanted, haystack)
            if item.kind is EvidenceKind.CLAIM:
                base = max(base, 0.15)  # extracted facts are always somewhat on-topic
            scores[item.evidence_id] = round(min(1.0, base * 1.5), 4)
        return scores


def normalize_url(url: str) -> str:
    """Key for URL deduplication (scheme/host case, fragment and trailing slash)."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return f"{parts.netloc.lower().removeprefix('www.')}{path}?{parts.query}".rstrip(
        "?"
    )


def _norm_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def score_source(
    *,
    url: str,
    domain: str,
    kind: SourceKind,
    snippet_only: bool,
) -> SourceCredibility:
    """Explainable credibility of a *source* (not of any claim made in it)."""
    path = urlsplit(url).path.lower()
    host = domain.lower()
    reg = registrable_domain(host)
    factors: list[str] = []
    if snippet_only:
        return SourceCredibility(
            score=0.25,
            tier=Tier.SNIPPET,
            factors=["only a search snippet was retrieved, not the page"],
        )
    if host.endswith(_GOV_SUFFIXES):
        return SourceCredibility(
            score=0.9, tier=Tier.GOVERNMENT, factors=["government/regulatory domain"]
        )
    if kind is SourceKind.OFFICIAL:
        factors.append("website verified as the subject's own")
        if "pricing" in path or "plans" in path:
            return SourceCredibility(
                score=0.85,
                tier=Tier.OFFICIAL_PRICING,
                factors=[*factors, "official pricing page (self-reported)"],
            )
        if any(h in path for h in _DOC_HINTS):
            return SourceCredibility(
                score=0.9,
                tier=Tier.OFFICIAL_DOCS,
                factors=[*factors, "official documentation"],
            )
        return SourceCredibility(
            score=0.75,
            tier=Tier.OFFICIAL_SITE,
            factors=[*factors, "marketing statements are self-reported, not audited"],
        )
    if reg in _DIRECTORIES:
        return SourceCredibility(
            score=0.4,
            tier=Tier.DIRECTORY,
            factors=["third-party directory or profile site (often self-submitted)"],
        )
    if kind is SourceKind.THIRD_PARTY:
        return SourceCredibility(
            score=0.5,
            tier=Tier.THIRD_PARTY,
            factors=["independent of the subject; reliability not assessed"],
        )
    return SourceCredibility(
        score=0.3, tier=Tier.UNKNOWN, factors=["ownership of the source is unknown"]
    )


def freshness(observed_at: datetime | None, now: datetime, horizon_days: int) -> float:
    """1.0 for new, decaying linearly to 0.0 at ``horizon_days``; 0.5 if unknown."""
    if observed_at is None:
        return 0.5
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age = max(0.0, (now - observed_at).total_seconds() / 86400)
    return round(max(0.0, 1 - age / horizon_days), 4)


def _best_excerpt(markdown: str, wanted: set[str]) -> str:
    """The paragraph (verbatim, capped) with the most objective terms."""
    best, best_score = "", -1.0
    for para in re.split(r"\n\s*\n", markdown):
        para = para.strip()
        if len(para) < 40 or para.startswith(("![", "|---")):
            continue
        score = overlap(wanted, terms(para))
        if score > best_score:
            best, best_score = para, score
    return best[:EXCERPT_CHARS]


class ContextBuilder:
    """Assemble, deduplicate, rank and bound the evidence for an analysis."""

    def __init__(
        self,
        research_repo: ResearchRepository,
        intelligence_repo: IntelligenceRepository,
        *,
        settings: Settings | None = None,
        retriever: EvidenceRetriever | None = None,
    ) -> None:
        self._research = research_repo
        self._intel = intelligence_repo
        self._settings = settings or get_settings()
        self._retriever = retriever or KeywordRetriever()

    async def build(
        self,
        job_ids: list[UUID],
        objective: str,
        *,
        max_sources: int | None = None,
        max_tokens: int | None = None,
        version: int = 1,
        now: datetime | None = None,
    ) -> UnifiedResearchContext:
        """Build the context for ``job_ids`` (first = the analysed research job)."""
        now = now or utc_now()
        max_sources = min(
            max_sources or self._settings.intelligence_max_sources,
            self._settings.intelligence_max_sources,
        )
        max_tokens = max_tokens or self._settings.intelligence_context_max_tokens
        sources: list[ResearchSource] = []
        source_job: dict[UUID, UUID] = {}
        claims: list[ResearchClaim] = []
        claim_job: dict[UUID, UUID] = {}
        entities = []
        websites = []
        queries: list[str] = []
        for job_id in job_ids:
            response = await self._research.get_response(job_id)
            if response is None:
                raise ResourceNotFoundException(f"Research job {job_id} was not found.")
            queries.append(response.query)
            for s in response.sources:
                sources.append(s)
                source_job[s.id] = job_id
            ws, ents, cls = await self._intel.load_material(job_id)
            websites += ws
            entities += ents
            for c in cls:
                claims.append(c)
                claim_job[c.claim_id] = job_id

        official = {
            registrable_domain(w.candidate.domain)
            for w in websites
            if w.candidate.official_status is OfficialStatus.VERIFIED
        }
        stats: dict[str, int | bool | float] = {
            "claims_loaded": len(claims),
            "sources_loaded": len(sources),
        }

        source_infos, source_texts, by_url = self._sources(
            sources, source_job, official, stats
        )
        items = self._claim_items(claims, claim_job, official, by_url, now)
        items = self._dedupe_claims(items, stats)
        wanted_text = f"{objective} {' '.join(queries)}"
        wanted = terms(wanted_text)
        items += self._excerpt_items(source_infos, sources, source_texts, wanted, now)

        for item in items:
            item.relevance = self._retriever.score(wanted_text, [item])[
                item.evidence_id
            ]
        self._rank(items, now)
        conflicts = self._conflicts(items, claims)
        conflict_ids = {i for c in conflicts for i in c.evidence_ids}
        selected, kept_sources = self._select(
            items, source_infos, conflict_ids, max_sources, max_tokens, stats
        )
        selected_ids = {i.evidence_id for i in selected}
        conflicts = [
            c.model_copy(
                update={
                    "evidence_ids": [e for e in c.evidence_ids if e in selected_ids]
                }
            )
            for c in conflicts
        ]
        conflicts = [c for c in conflicts if len(c.evidence_ids) >= 2]

        company_names = [
            e.canonical_name for e in entities if e.entity_type is EntityType.COMPANY
        ] or sorted({w.candidate.website_name for w in websites})

        def names(kind: EntityType) -> list[str]:
            return sorted(
                {e.canonical_name for e in entities if e.entity_type is kind}
            )[:MAX_ENTITY_LIST]

        ctx = UnifiedResearchContext(
            research_job_id=job_ids[0],
            job_ids=list(job_ids),
            objective=objective,
            version=version,
            companies=sorted(set(company_names))[:MAX_ENTITY_LIST],
            products=names(EntityType.PRODUCT),
            services=names(EntityType.SERVICE),
            technologies=names(EntityType.TECHNOLOGY),
            evidence=selected,
            sources=kept_sources,
            verified_ids=[
                i.evidence_id for i in selected if i.verification_status in _VERIFIED
            ],
            unverified_ids=[
                i.evidence_id
                for i in selected
                if i.verification_status not in _VERIFIED
                and i.verification_status is not VerificationStatus.CONFLICTING
            ],
            conflicts=conflicts,
            source_texts={
                s.url: source_texts[s.url]
                for s in kept_sources
                if s.url in source_texts
            },
            created_at=now,
        )
        ctx.missing_information = self._missing(ctx, claims)
        ctx.token_estimate = sum(i.token_estimate for i in selected)
        ctx.context_hash = content_hash(
            objective + "|" + ",".join(sorted(i.evidence_id for i in selected))
        )
        stats.update(
            evidence_selected=len(selected),
            sources_selected=len(kept_sources),
            conflicts=len(conflicts),
            missing_information=len(ctx.missing_information),
            token_estimate=ctx.token_estimate,
            max_tokens=max_tokens,
        )
        ctx.statistics = dict(stats)
        return ctx

    # -- sources --------------------------------------------------------------------

    def _sources(
        self,
        sources: list[ResearchSource],
        source_job: dict[UUID, UUID],
        official: set[str],
        stats: dict,
    ) -> tuple[list[SourceInfo], dict[str, str], dict[str, SourceInfo]]:
        infos: list[SourceInfo] = []
        texts: dict[str, str] = {}
        seen_urls: dict[str, SourceInfo] = {}
        seen_hashes: set[str] = set()
        duplicates = 0
        for s in sources:
            url = s.final_url or s.normalized_url or s.original_url
            key = normalize_url(url)
            has_text = bool(
                s.extracted_markdown
                and s.extraction_status is ExtractionStatus.SUCCEEDED
            )
            if key in seen_urls or (
                has_text and s.content_hash and s.content_hash in seen_hashes
            ):
                duplicates += 1
                continue
            if not has_text and not s.snippet:
                continue  # nothing citable
            domain = (s.domain or urlsplit(url).netloc).lower()
            kind = (
                SourceKind.OFFICIAL
                if registrable_domain(domain) in official
                else SourceKind.UNKNOWN
            )
            info = SourceInfo(
                source_id=s.id,
                research_job_id=source_job[s.id],
                url=url,
                title=s.title,
                domain=domain,
                kind=kind,
                content_hash=s.content_hash,
                extracted_at=s.extracted_at,
                has_full_text=has_text,
                credibility=score_source(
                    url=url, domain=domain, kind=kind, snippet_only=not has_text
                ),
            )
            infos.append(info)
            seen_urls[key] = info
            if has_text and s.content_hash:
                seen_hashes.add(s.content_hash)
            if has_text and s.extracted_markdown:
                texts[url] = s.extracted_markdown
        stats["duplicate_sources_removed"] = duplicates
        return infos, texts, seen_urls

    # -- evidence ---------------------------------------------------------------------

    def _claim_items(
        self,
        claims: list[ResearchClaim],
        claim_job: dict[UUID, UUID],
        official: set[str],
        by_url: dict[str, SourceInfo],
        now: datetime,
    ) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for c in claims:
            info = by_url.get(normalize_url(c.source_url))
            kind = c.source_kind
            if (
                kind is SourceKind.UNKNOWN
                and registrable_domain(c.source_domain) in official
            ):
                kind = SourceKind.OFFICIAL
            items.append(
                EvidenceItem(
                    evidence_id=f"c-{c.claim_id.hex[:10]}",
                    kind=EvidenceKind.CLAIM,
                    research_job_id=claim_job[c.claim_id],
                    claim_id=c.claim_id,
                    source_id=info.source_id if info else None,
                    entity=c.subject_entity,
                    entity_type=c.subject_type,
                    predicate=c.predicate,
                    value=c.object_value,
                    text=c.claim_text,
                    excerpt=c.evidence_excerpt,
                    source_url=c.source_url,
                    source_title=c.source_title,
                    source_domain=c.source_domain,
                    source_content_hash=c.source_content_hash,
                    source_kind=kind,
                    verification_status=c.verification_status,
                    extraction_confidence=c.confidence_score,
                    observed_at=c.observed_at,
                    publication_date=c.publication_date,
                    credibility=score_source(
                        url=c.source_url,
                        domain=c.source_domain,
                        kind=kind,
                        snippet_only=False,
                    ),
                    token_estimate=estimate_tokens(c.claim_text + c.evidence_excerpt)
                    + 12,
                )
            )
        return items

    @staticmethod
    def _dedupe_claims(items: list[EvidenceItem], stats: dict) -> list[EvidenceItem]:
        """Drop exact repeats (same subject, predicate, value and source URL).

        Equivalent claims from *different* sources are kept: independent
        corroboration is information, and each source must stay citable.
        """
        seen: set[tuple[str, str, str, str]] = set()
        kept: list[EvidenceItem] = []
        for i in items:
            key = (
                _norm_value(i.entity or ""),
                i.predicate or "",
                _norm_value(i.text),
                normalize_url(i.source_url),
            )
            if key in seen:
                continue
            seen.add(key)
            kept.append(i)
        stats["duplicate_claims_removed"] = len(items) - len(kept)
        return kept

    def _excerpt_items(
        self,
        infos: list[SourceInfo],
        sources: list[ResearchSource],
        texts: dict[str, str],
        wanted: set[str],
        now: datetime,
    ) -> list[EvidenceItem]:
        by_id = {s.id: s for s in sources}
        items: list[EvidenceItem] = []
        for info in infos:
            raw = by_id.get(info.source_id) if info.source_id else None
            text = texts.get(info.url)
            excerpt = (
                _best_excerpt(text, wanted) if text else (raw.snippet if raw else "")
            )
            if not excerpt or not info.source_id:
                continue
            items.append(
                EvidenceItem(
                    evidence_id=f"s-{info.source_id.hex[:10]}",
                    kind=EvidenceKind.SOURCE_EXCERPT,
                    research_job_id=info.research_job_id,
                    source_id=info.source_id,
                    text=excerpt,
                    excerpt=excerpt,
                    source_url=info.url,
                    source_title=info.title,
                    source_domain=info.domain,
                    source_content_hash=info.content_hash,
                    source_kind=info.kind,
                    verification_status=VerificationStatus.UNVERIFIED,
                    observed_at=info.extracted_at,
                    credibility=info.credibility,
                    token_estimate=estimate_tokens(excerpt) + 12,
                )
            )
        return items

    @staticmethod
    def _rank(items: list[EvidenceItem], now: datetime) -> None:
        horizon = get_settings().intelligence_freshness_days
        for i in items:
            direct = (
                _DIRECTNESS[i.verification_status]
                if i.kind is EvidenceKind.CLAIM
                else (0.2 if i.credibility.tier is Tier.SNIPPET else 0.5)
            )
            i.rank_score = round(
                0.50 * i.relevance
                + 0.25 * i.credibility.score
                + 0.10 * freshness(i.observed_at, now, horizon)
                + 0.15 * direct,
                4,
            )

    # -- conflicts / selection / gaps ----------------------------------------------------

    @staticmethod
    def _conflicts(
        items: list[EvidenceItem], claims: list[ResearchClaim]
    ) -> list[Contradiction]:
        """Conflicts: claims Module 3 marked conflicting, plus differing values of
        single-valued predicates (headquarters, founded year, company size)."""
        groups: dict[tuple[str, str], list[EvidenceItem]] = {}
        for i in items:
            if i.kind is not EvidenceKind.CLAIM or not i.predicate:
                continue
            if (
                i.predicate in SINGLE_VALUED
                or i.verification_status is VerificationStatus.CONFLICTING
            ):
                groups.setdefault(
                    (_norm_value(i.entity or ""), i.predicate), []
                ).append(i)
        out: list[Contradiction] = []
        for (_, predicate), group in sorted(groups.items()):
            values = {_norm_value(g.text): g.text for g in group}
            if len(values) < 2:
                continue
            subject = group[0].entity or "unknown"
            out.append(
                Contradiction(
                    subject=f"{subject} — {predicate.replace('_', ' ')}",
                    description="Sources state different values; none is chosen here.",
                    evidence_ids=[g.evidence_id for g in group],
                    claim_ids=[g.claim_id for g in group if g.claim_id],
                    source_urls=sorted({g.source_url for g in group}),
                    values=sorted(values.values()),
                )
            )
        return out

    @staticmethod
    def _select(
        items: list[EvidenceItem],
        infos: list[SourceInfo],
        priority_ids: set[str],
        max_sources: int,
        max_tokens: int,
        stats: dict,
    ) -> tuple[list[EvidenceItem], list[SourceInfo]]:
        """Greedy by rank under source-count and token limits; conflicts first."""
        ordered = sorted(
            items,
            key=lambda i: (
                i.evidence_id not in priority_ids,
                -i.rank_score,
                i.evidence_id,
            ),
        )
        source_rank: dict[str, float] = {}
        for i in ordered:
            source_rank.setdefault(normalize_url(i.source_url), i.rank_score)
        allowed = {
            u
            for u, _ in sorted(source_rank.items(), key=lambda kv: (-kv[1], kv[0]))[
                :max_sources
            ]
        }
        selected: list[EvidenceItem] = []
        used = 0
        for i in ordered:
            if normalize_url(i.source_url) not in allowed:
                continue
            if used + i.token_estimate > max_tokens:
                continue
            selected.append(i)
            used += i.token_estimate
        selected.sort(key=lambda i: (-i.rank_score, i.evidence_id))
        stats["truncated"] = len(selected) < len(items)
        stats["evidence_considered"] = len(items)
        by_source: dict[str, list[str]] = {}
        for i in selected:
            by_source.setdefault(normalize_url(i.source_url), []).append(i.evidence_id)
        kept: list[SourceInfo] = []
        known: set[str] = set()
        for s in infos:
            k = normalize_url(s.url)
            if k in by_source:
                kept.append(s.model_copy(update={"evidence_ids": by_source[k]}))
                known.add(k)
        for k, ids in by_source.items():  # claims whose page was not stored
            if k in known:
                continue
            first = next(i for i in selected if normalize_url(i.source_url) == k)
            kept.append(
                SourceInfo(
                    research_job_id=first.research_job_id,
                    url=first.source_url,
                    title=first.source_title,
                    domain=first.source_domain,
                    kind=first.source_kind,
                    content_hash=first.source_content_hash,
                    credibility=first.credibility,
                    evidence_ids=ids,
                )
            )
        return selected, kept

    @staticmethod
    def _missing(
        ctx: UnifiedResearchContext, claims: list[ResearchClaim]
    ) -> list[InformationGap]:
        have: dict[str, set[str]] = {}
        for c in claims:
            have.setdefault(_norm_value(c.subject_entity), set()).add(c.predicate)
            if c.predicate in ("offers_service",):
                have[_norm_value(c.subject_entity)].add("offers_product")
        gaps: list[InformationGap] = []
        for company in ctx.companies:
            present = have.get(_norm_value(company), set())
            for predicate, label, critical in REQUIRED_FACTS:
                if predicate not in present:
                    gaps.append(
                        InformationGap(
                            description=f"No {label} found in collected evidence for {company}.",
                            related_entity=company,
                            predicate=predicate,
                            reason="Not publicly found in the collected sources.",
                            critical=critical,
                            priority=2 if critical else 4,
                        )
                    )
        return gaps
