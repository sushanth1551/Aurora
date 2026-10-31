"""Website discovery: find, rank and (cautiously) classify relevant websites.

Reuses the Module 2 SerpApi adapter and ``SourceManager``. The first search
result is never assumed to be the official site: a site is ``verified`` only
when its domain matches the requested name *and* the search result text
mentions that name; third-party directories/social/review sites are labelled
``third_party``; everything else stays ``unverified``.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.core.metrics import timed
from app.research.domains import (
    domain_label,
    name_tokens,
    normalize_name,
    registrable_domain,
)
from app.research.research_service import SearchProvider
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlRejected
from app.schemas.intelligence import (
    DiscoverWebsitesRequest,
    OfficialStatus,
    SubjectType,
    WebsiteCandidate,
    WebsiteType,
)
from app.schemas.research import SearchResult

logger = logging.getLogger(__name__)

#: Search-engine hosts whose result/redirect URLs are never a company site.
SEARCH_ENGINE_DOMAINS = frozenset(
    {"google.com", "bing.com", "duckduckgo.com", "yahoo.com", "baidu.com", "yandex.com"}
)
_SEARCH_WRAPPER_PATHS = (
    "/url", "/search", "/aclk", "/imgres", "/maps", "/travel", "/shopping",
    "/webhp", "/l.php", "/ck/a",
)  # fmt: skip
RESULTS_PER_QUERY = 10
MAX_QUERIES = 12

#: Domains that describe companies rather than being them.
THIRD_PARTY_DOMAINS: dict[str, WebsiteType] = {
    "wikipedia.org": WebsiteType.DIRECTORY,
    "crunchbase.com": WebsiteType.DIRECTORY,
    "g2.com": WebsiteType.DIRECTORY,
    "capterra.com": WebsiteType.DIRECTORY,
    "softwareadvice.com": WebsiteType.DIRECTORY,
    "gartner.com": WebsiteType.DIRECTORY,
    "clutch.co": WebsiteType.DIRECTORY,
    "tracxn.com": WebsiteType.DIRECTORY,
    "trustpilot.com": WebsiteType.DIRECTORY,
    "glassdoor.com": WebsiteType.DIRECTORY,
    "ambitionbox.com": WebsiteType.DIRECTORY,
    "producthunt.com": WebsiteType.DIRECTORY,
    "linkedin.com": WebsiteType.SOCIAL,
    "facebook.com": WebsiteType.SOCIAL,
    "twitter.com": WebsiteType.SOCIAL,
    "x.com": WebsiteType.SOCIAL,
    "instagram.com": WebsiteType.SOCIAL,
    "youtube.com": WebsiteType.SOCIAL,
    "reddit.com": WebsiteType.SOCIAL,
    "quora.com": WebsiteType.SOCIAL,
    "github.com": WebsiteType.SOCIAL,
    "medium.com": WebsiteType.NEWS,
    "forbes.com": WebsiteType.NEWS,
    "techcrunch.com": WebsiteType.NEWS,
    "economictimes.com": WebsiteType.NEWS,
    "indiatimes.com": WebsiteType.NEWS,
    "amazon.com": WebsiteType.MARKETPLACE,
    "amazon.in": WebsiteType.MARKETPLACE,
    # Search-engine properties and app stores (play.google.com, maps.google.com,
    # support.google.com, ...) are never a company's own website.
    "google.com": WebsiteType.MARKETPLACE,
}

#: Individual hosts of otherwise unrelated registrable domains.
THIRD_PARTY_HOSTS: dict[str, WebsiteType] = {
    "apps.apple.com": WebsiteType.MARKETPLACE,
    "itunes.apple.com": WebsiteType.MARKETPLACE,
}


@dataclass
class DiscoveryResult:
    """Candidates plus the raw search material they came from."""

    candidates: list[WebsiteCandidate] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    rejected_count: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class _Hit:
    query: str
    target: str | None
    result: SearchResult
    url: str
    host: str


def build_queries(request: DiscoverWebsitesRequest) -> list[tuple[str, str | None]]:
    """Return ``(search query, target name or None)`` pairs for discovery."""
    pairs: list[tuple[str, str | None]] = []
    if request.target_names:
        for name in request.target_names:
            pairs.append((f"{name} official website", name))
            if request.subject_type != SubjectType.COMPANY:
                pairs.append((f"{name} {request.subject_type.value}", name))
            else:
                pairs.append((f"{name} products pricing", name))
    else:
        query = request.query
        kind = request.subject_type
        target = query if kind in (SubjectType.COMPANY, SubjectType.PRODUCT) else None
        pairs.append((query, target))
        if kind == SubjectType.COMPANY:
            pairs.append((f"{query} official website", target))
        elif kind == SubjectType.PRODUCT:
            pairs.append((f"{query} pricing features", target))
        elif kind == SubjectType.TECHNOLOGY:
            pairs.append((f"{query} documentation", None))
        elif kind == SubjectType.SERVICE:
            pairs.append((f"{query} service provider", None))
        else:
            pairs.append((f"top {query} companies", None))
    unique: list[tuple[str, str | None]] = []
    for pair in pairs:
        if pair[0] not in {u[0] for u in unique}:
            unique.append(pair)
    return unique[:MAX_QUERIES]


_BRAND_PREFIXES = ("get", "try", "use", "go", "hello", "meet", "www")
_BRAND_SUFFIXES = (
    "hq", "app", "io", "ai", "labs", "tech", "software", "inc", "global",
    "group", "corp", "online", "cloud", "platform", "systems", "technologies",
)  # fmt: skip


def _label_matches(label: str, wanted: str) -> bool:
    """Domain label equals the name, or the name plus a common brand affix.

    Deliberately strict: ``acme-reviews`` or ``bestacme`` do not match ``acme``.
    """
    if label == wanted:
        return True
    return any(label == p + wanted for p in _BRAND_PREFIXES) or any(
        label == wanted + s for s in _BRAND_SUFFIXES
    )


def is_search_engine_wrapper(url: str) -> bool:
    """True for search-engine result pages and redirect/tracking links."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if registrable_domain(host) not in SEARCH_ENGINE_DOMAINS:
        return False
    return parts.path.startswith(_SEARCH_WRAPPER_PATHS)


def _third_party_type(host: str, target: str | None = None) -> WebsiteType | None:
    """Third-party type of ``host``, unless its domain is the requested name itself."""
    host = host.lower()
    kind = THIRD_PARTY_HOSTS.get(host) or THIRD_PARTY_DOMAINS.get(
        registrable_domain(host)
    )
    if kind is not None and target:
        apex = registrable_domain(host)
        wanted = normalize_name(target)
        # Only the apex (or www) host can be the named company's own site; a
        # subdomain such as play.google.com is still a third-party property.
        if host in (apex, f"www.{apex}") and wanted:
            if _label_matches(normalize_name(domain_label(host)), wanted):
                return None
    return kind


def _clean_title(title: str | None, fallback: str) -> str:
    if not title:
        return fallback
    first = re.split(r"\s[|\-–—:·]\s", title.strip(), maxsplit=1)[0].strip()
    return (first or fallback)[:80]


class WebsiteDiscovery:
    """Discover candidate websites for a research subject."""

    def __init__(
        self,
        search: SearchProvider,
        sources: SourceManager,
        settings: Settings | None = None,
    ) -> None:
        self._search = search
        self._sources = sources
        self._settings = settings or get_settings()

    async def discover(self, request: DiscoverWebsitesRequest) -> DiscoveryResult:
        """Run discovery searches, validate URLs, and rank one candidate per site."""
        limit = request.max_websites or self._settings.website_research_max_websites
        limit = min(limit, self._settings.website_research_max_websites)
        queries = build_queries(request)
        gate = asyncio.Semaphore(
            self._settings.website_research_max_concurrent_searches
        )
        outcome = DiscoveryResult()

        async def run(
            query: str, target: str | None
        ) -> list[tuple[str, str | None, SearchResult]]:
            async with gate:
                with timed("search"):
                    results = await self._search.search(
                        query,
                        num_results=RESULTS_PER_QUERY,
                        country=request.country,
                        language=request.language,
                    )
            return [(query, target, r) for r in results]

        batches = await asyncio.gather(*(run(q, t) for q, t in queries))
        flat = [item for batch in batches for item in batch]
        for query, _, result in flat:
            raw = result.model_copy(deep=True)
            raw.metadata["search_query"] = query
            outcome.search_results.append(raw)

        prepared = await self._sources.prepare(
            [r for _, _, r in flat], max_sources=len(flat) or 1
        )
        outcome.rejected_count = len(prepared.rejected)
        if prepared.rejected:
            outcome.notes.append(
                f"{len(prepared.rejected)} search result URL(s) were rejected by URL policy."
            )
        valid = {c.validated.normalized for c in prepared.selected if c.validated}
        hits: list[_Hit] = []
        wrappers = 0
        for query, target, result in flat:
            if is_search_engine_wrapper(result.url):
                wrappers += 1
                continue
            normalized = self._normalized_for(result.url, valid)
            if normalized is None:
                continue
            host = urlsplit(normalized).hostname or ""
            hits.append(_Hit(query, target, result, normalized, host))

        if wrappers:
            outcome.notes.append(
                f"{wrappers} search-engine result/redirect URL(s) were ignored."
            )
        candidates = await self._rank(hits, request, outcome)
        outcome.candidates = candidates[:limit]
        log_event(
            logger,
            "website_discovery",
            queries=len(queries),
            results=len(flat),
            candidates=len(outcome.candidates),
            rejected=outcome.rejected_count,
        )
        return outcome

    def _normalized_for(self, url: str, valid: set[str]) -> str | None:
        """Normalized form of a raw result URL, if it passed validation."""
        try:
            normalized, _ = self._sources.policy.normalize(url)
        except UrlRejected:
            return None
        return normalized if normalized in valid else None

    async def _rank(
        self,
        hits: list[_Hit],
        request: DiscoverWebsitesRequest,
        outcome: DiscoveryResult,
    ) -> list[WebsiteCandidate]:
        groups: dict[tuple[str, str | None], list[_Hit]] = {}
        for hit in hits:
            groups.setdefault((registrable_domain(hit.host), hit.target), []).append(
                hit
            )
        candidates: list[WebsiteCandidate] = []
        for (domain, target), group in groups.items():
            candidate = await self._build_candidate(domain, target, group, request)
            if candidate is not None:
                candidates.append(candidate)
        # Keep the best candidate per domain (a domain may match several targets).
        best: dict[str, WebsiteCandidate] = {}
        for cand in sorted(candidates, key=lambda c: -c.relevance_score):
            best.setdefault(cand.domain, cand)
        ranked = sorted(
            best.values(), key=lambda c: (-c.relevance_score, c.search_position)
        )
        third_party = [
            c for c in ranked if c.official_status == OfficialStatus.THIRD_PARTY
        ]
        if third_party:
            outcome.notes.append(
                f"{len(third_party)} third-party site(s) (directories, social, news) "
                "were excluded from the website list."
            )
        verified: dict[str, list[str]] = {}
        for cand in ranked:
            wanted = cand.metadata.get("target_name")
            if cand.official_status == OfficialStatus.VERIFIED and wanted:
                verified.setdefault(str(wanted), []).append(cand.domain)
        for wanted, domains in verified.items():
            if len(domains) > 1:
                outcome.notes.append(
                    f"'{wanted}' is ambiguous: {len(domains)} different domains look "
                    f"official ({', '.join(domains[:4])}); review before relying on them."
                )
        mismatched = [c for c in ranked if c.official_status == OfficialStatus.MISMATCH]
        if mismatched:
            outcome.notes.append(
                f"{len(mismatched)} result site(s) were dropped because neither their "
                "domain nor their search text relates to the requested name."
            )
        return [
            c
            for c in ranked
            if c.official_status
            not in (OfficialStatus.THIRD_PARTY, OfficialStatus.MISMATCH)
        ]

    async def _build_candidate(
        self,
        domain: str,
        target: str | None,
        group: list[_Hit],
        request: DiscoverWebsitesRequest,
    ) -> WebsiteCandidate | None:
        top = min(group, key=lambda h: h.result.position)
        third_party = _third_party_type(top.host, target)
        homepage = f"{urlsplit(top.url).scheme}://{urlsplit(top.url).netloc}/"
        try:
            await self._sources.policy.validate(homepage)
        except UrlRejected:
            return None
        text = " ".join(
            filter(
                None, [top.result.title, top.result.snippet, top.result.displayed_link]
            )
        )
        reasons: list[str] = [
            f"search position {top.result.position} for '{top.query}'"
        ]
        score = 0.3 / (1 + 0.15 * (top.result.position - 1))
        name = target or _clean_title(top.result.title, domain_label(top.host).title())
        status = OfficialStatus.UNVERIFIED
        website_type = WebsiteType.UNKNOWN
        confidence = 0.3
        if third_party is not None:
            status = OfficialStatus.THIRD_PARTY
            website_type = third_party
            score = min(score, 0.2)
            reasons.append(f"third-party {third_party.value} site")
            confidence = 0.9
        else:
            query_tokens = name_tokens(target or request.query)
            haystack = set(name_tokens(text))
            overlap = (
                sum(t in haystack for t in query_tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            score += 0.2 * overlap
            if overlap:
                reasons.append(
                    f"{overlap:.0%} of the query terms appear in the result text"
                )
            if target:
                status, bonus, why = self._assess_official(target, top.host, text)
                score += bonus
                reasons.extend(why)
                confidence = {
                    OfficialStatus.VERIFIED: 0.85,
                    OfficialStatus.MISMATCH: 0.7,
                }.get(status, 0.35)
                if status == OfficialStatus.MISMATCH:
                    score = min(score, 0.1)
            if status == OfficialStatus.VERIFIED:
                website_type = WebsiteType.OFFICIAL
            elif top.host.startswith(("docs.", "developer.", "developers.")):
                website_type = WebsiteType.DOCUMENTATION
        queries_hit = len({h.query for h in group})
        if queries_hit > 1:
            score += min(0.1, 0.05 * (queries_hit - 1))
            reasons.append(f"returned for {queries_hit} different queries")
        return WebsiteCandidate(
            website_name=name,
            domain=domain,
            homepage_url=homepage,
            title=top.result.title,
            description=top.result.snippet,
            search_query=top.query,
            search_position=top.result.position,
            relevance_score=round(min(score, 1.0), 3),
            website_type=website_type,
            official_status=status,
            confidence=confidence,
            selection_reasons=reasons,
            metadata={
                "target_name": target,
                "result_urls": sorted({h.url for h in group})[:10],
                "positions": sorted({h.result.position for h in group}),
                "official_unverified_reason": (
                    "official ownership not established from search evidence"
                    if status == OfficialStatus.UNVERIFIED
                    else None
                ),
            },
        )

    @staticmethod
    def _assess_official(
        target: str, host: str, text: str
    ) -> tuple[OfficialStatus, float, list[str]]:
        """Decide official status from name/domain/result-text evidence only."""
        wanted = normalize_name(target)
        label = normalize_name(domain_label(host))
        reasons: list[str] = []
        domain_match = bool(wanted) and _label_matches(label, wanted)
        text_mentions = bool(wanted) and wanted in normalize_name(text)
        if domain_match:
            reasons.append(f"domain '{domain_label(host)}' matches the name '{target}'")
        if text_mentions:
            reasons.append("search result text mentions the name")
        if domain_match and text_mentions:
            return OfficialStatus.VERIFIED, 0.4, reasons
        if domain_match:
            return (
                OfficialStatus.UNVERIFIED,
                0.2,
                reasons
                + ["name matches the domain but the result text does not confirm it"],
            )
        if not text_mentions:
            return (
                OfficialStatus.MISMATCH,
                0.0,
                [f"neither the domain nor the result text relates to '{target}'"],
            )
        return OfficialStatus.UNVERIFIED, 0.0, reasons

    @staticmethod
    def confirm_with_homepage(
        candidate: WebsiteCandidate, title: str | None, text: str
    ) -> WebsiteCandidate:
        """Upgrade ``unverified`` to ``verified`` using fetched homepage branding.

        Requires the requested name to appear in the homepage title/heading
        *and* the domain label to relate to it. Never downgrades.
        """
        target = candidate.metadata.get("target_name")
        if (
            candidate.official_status != OfficialStatus.UNVERIFIED
            or not isinstance(target, str)
            or not target
        ):
            return candidate
        wanted = normalize_name(target)
        label = normalize_name(domain_label(candidate.domain))
        branded = wanted and wanted in normalize_name(f"{title or ''} {text[:500]}")
        related = bool(wanted) and _label_matches(label, wanted)
        if branded and related:
            updated = candidate.model_copy(deep=True)
            updated.official_status = OfficialStatus.VERIFIED
            updated.website_type = WebsiteType.OFFICIAL
            updated.confidence = max(updated.confidence, 0.8)
            updated.selection_reasons.append(
                "homepage branding contains the name and the domain relates to it"
            )
            return updated
        return candidate
