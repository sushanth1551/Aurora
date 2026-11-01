"""SERP-based analysis: clustering, competitors, content gaps and opportunities.

All inputs are stored SERP snapshots (organic results + features). Everything is
deterministic and every conclusion carries the evidence it came from. No search
volume, difficulty or traffic figure is created; ranking positions are the
positions *observed* in one snapshot at its timestamp, location and device.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.seo.keywords.models import (
    Candidate,
    Competitor,
    GapEvidence,
    GapTerm,
    Level,
    Opportunity,
    SerpEvidence,
    unavailable_metrics,
)
from app.seo.keywords.text import (
    STOPWORDS,
    bare_host,
    bigrams,
    comparable_url,
    content_tokens,
    normalize_keyword,
)
from app.tools.serpapi_features import SerpSnapshotData

MIN_SHARED_URLS = 3  # SERP-overlap clustering: results two queries have in common
MIN_GAP_DOMAINS = 2  # a phrase must appear for this many distinct competitor domains
MAX_GAP_TERMS = 40
MAX_GAP_EVIDENCE = 4
QUESTION_COVERAGE = 0.6  # share of a question's content words found on the site
TOP_N = 10


@dataclass
class Snapshot:
    """A stored SERP with its database id (``None`` before persistence)."""

    id: str | None
    data: SerpSnapshotData
    cache_hit: bool = False
    observed_at: object | None = None


# -- clustering ----------------------------------------------------------------------


def cluster_keywords(snaps: dict[str, Snapshot]) -> dict[str, str]:
    """Group keywords whose top results share at least ``MIN_SHARED_URLS`` URLs.

    Single-link union-find over ``normalized keyword -> snapshot``. The cluster
    key is the shortest member (ties alphabetical), so it is stable.
    """
    keys = sorted(snaps)
    urls = {
        k: {comparable_url(r.url) for r in snaps[k].data.organic[:TOP_N]} for k in keys
    }
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if len(urls[a] & urls[b]) >= MIN_SHARED_URLS:
                parent[find(b)] = find(a)
    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)
    label = {
        root: min(members, key=lambda m: (len(m), m))
        for root, members in groups.items()
    }
    return {k: label[find(k)] for k in keys}


# -- competitors ------------------------------------------------------------------------


def find_competitors(snaps: dict[str, Snapshot], site_host: str) -> list[Competitor]:
    """Domains observed in the top results, excluding the audited site.

    "Competitor" here means *SERP competitor*: it ranked for the same queries.
    Whether it is a business competitor is a different question (graph data).
    """
    site = bare_host(site_host)
    positions: dict[str, dict[str, int]] = defaultdict(dict)
    for keyword, snap in snaps.items():
        for result in snap.data.organic[:TOP_N]:
            domain = bare_host(result.domain or "")
            if domain and domain != site:
                positions[domain].setdefault(keyword, result.position)
    out = [
        Competitor(
            domain=domain,
            appearances=len(by_kw),
            keywords=sorted(by_kw),
            best_position=min(by_kw.values()),
            positions=dict(sorted(by_kw.items())),
        )
        for domain, by_kw in positions.items()
    ]
    return sorted(out, key=lambda c: (-c.appearances, c.best_position, c.domain))


def compare_competitors(
    competitors: list[Competitor], domains: list[str]
) -> dict[str, object]:
    """Keyword coverage matrix for chosen domains: who ranks for what, where."""
    wanted = [bare_host(d) for d in domains]
    chosen = [c for c in competitors if c.domain in wanted]
    all_keywords = sorted({k for c in chosen for k in c.keywords})
    shared = (
        [
            k
            for k in all_keywords
            if sum(k in c.positions for c in chosen) == len(chosen)
        ]
        if chosen
        else []
    )
    return {
        "domains": [c.domain for c in chosen],
        "unknown_domains": [d for d in wanted if d not in {c.domain for c in chosen}],
        "keywords": all_keywords,
        "shared_keywords": shared,
        "matrix": {c.domain: c.positions for c in chosen},
        "only_for": {
            c.domain: [
                k
                for k in c.keywords
                if all(k not in o.positions for o in chosen if o is not c)
            ]
            for c in chosen
        },
        "basis": "observed positions in stored SERP snapshots (top 10), not rankings over time",
    }


# -- content gaps -------------------------------------------------------------------------


def detect_gaps(
    snaps: dict[str, Snapshot], site_host: str, site_text: list[str]
) -> tuple[list[GapTerm], str]:
    """Phrases and questions competitors' results cover that the site's own text does not.

    Returns ``(gaps, status)``. With no site text (no stored audit) status is
    ``site_content_unavailable`` and the list is empty: "the site lacks it"
    cannot be claimed without having read the site. Evidence is the verbatim
    competitor title/snippet or the People Also Ask question.
    """
    if not site_text:
        return [], "site_content_unavailable"
    site = bare_host(site_host)
    site_joined = " " + " ".join(normalize_keyword(t) for t in site_text) + " "
    site_tokens = set(" ".join(site_text).split()) | {
        w for t in site_text for w in content_tokens(t)
    }

    phrase_domains: dict[str, set[str]] = defaultdict(set)
    phrase_queries: dict[str, set[str]] = defaultdict(set)
    phrase_evidence: dict[str, list[GapEvidence]] = defaultdict(list)
    for keyword, snap in snaps.items():
        query_words = set(keyword.split())
        for result in snap.data.organic[:TOP_N]:
            domain = bare_host(result.domain or "")
            if not domain or domain == site:
                continue
            for field in (result.title, result.snippet or ""):
                for phrase in bigrams(field):
                    if set(phrase.split()) <= query_words:
                        continue  # restates the query that produced this result
                    phrase_domains[phrase].add(domain)
                    phrase_queries[phrase].add(keyword)
                    if len(phrase_evidence[phrase]) < MAX_GAP_EVIDENCE and all(
                        e.domain != domain for e in phrase_evidence[phrase]
                    ):
                        phrase_evidence[phrase].append(
                            GapEvidence(
                                domain=domain,
                                url=result.url,
                                query=keyword,
                                position=result.position,
                                excerpt=field[:300],
                            )
                        )
    gaps: list[GapTerm] = []
    for phrase, domains in phrase_domains.items():
        if len(domains) < MIN_GAP_DOMAINS or f" {phrase} " in site_joined:
            continue
        gaps.append(
            GapTerm(
                term=phrase,
                kind="phrase",
                competitor_domains=sorted(domains),
                queries=sorted(phrase_queries[phrase]),
                evidence=phrase_evidence[phrase],
            )
        )
    gaps.sort(key=lambda g: (-len(g.competitor_domains), -len(g.queries), g.term))
    gaps = gaps[:MAX_GAP_TERMS]

    seen_questions: set[str] = set()
    for keyword, snap in snaps.items():
        for item in snap.data.features.people_also_ask:
            norm = normalize_keyword(item.question)
            words = [w for w in content_tokens(item.question)]
            if norm in seen_questions or len(words) < 2:
                continue
            covered = sum(
                1 for w in words if w in site_tokens or f" {w} " in site_joined
            ) / len(words)
            if covered < QUESTION_COVERAGE:
                seen_questions.add(norm)
                domain = bare_host(item.domain or "")
                gaps.append(
                    GapTerm(
                        term=item.question,
                        kind="question",
                        competitor_domains=[domain]
                        if domain and domain != site
                        else [],
                        queries=[keyword],
                        basis="inferred",
                        evidence=[
                            GapEvidence(
                                domain=domain or "",
                                url=item.url or "",
                                query=keyword,
                                position=0,
                                excerpt=(item.snippet or item.question)[:300],
                            )
                        ],
                    )
                )
    return gaps, "ok"


# -- opportunities --------------------------------------------------------------------------

_TIER_ORDER = (
    "write_new",
    "improve_existing",
    "monitor",
    "low_evidence",
    "deprioritized",
)
_LEVELS: tuple[Level, Level, Level] = ("low", "medium", "high")


def _lower(level: Level) -> Level:
    return _LEVELS[max(0, _LEVELS.index(level) - 1)]


def site_position(snap: SerpSnapshotData, site_host: str) -> int | None:
    site = bare_host(site_host)
    positions = [
        r.position for r in snap.organic[:TOP_N] if bare_host(r.domain or "") == site
    ]
    return min(positions) if positions else None


def evidence_for(keyword: str, snap: Snapshot, site_host: str) -> SerpEvidence:
    data = snap.data
    ai = data.features.ai_overview
    site = bare_host(site_host)
    return SerpEvidence(
        snapshot_id=snap.id,
        query=keyword,
        organic_count=len(data.organic),
        top_domains=[
            bare_host(r.domain or "") for r in data.organic[:TOP_N] if r.domain
        ],
        site_position=site_position(data, site_host),
        paa_count=len(data.features.people_also_ask),
        related_count=len(data.features.related_searches),
        featured_snippet_domain=(
            data.features.featured_snippet.domain
            if data.features.featured_snippet
            else None
        ),
        ai_overview=ai.status,
        ai_overview_cites_site=(
            site in {bare_host(d) for d in ai.cited_domains}
            if ai.status == "present"
            else None
        ),
        shopping=data.features.shopping_results,
        ads=data.features.ads_top + data.features.ads_bottom,
        cache_hit=snap.cache_hit,
    )


def build_opportunities(
    candidates: list[Candidate],
    snaps: dict[str, Snapshot],
    gaps: list[GapTerm],
    site_host: str,
) -> list[Opportunity]:
    """One opportunity per searched keyword, ranked by tier, impact and confidence."""
    out: list[Opportunity] = []
    for cand in candidates:
        snap = snaps.get(cand.normalized)
        if snap is None:
            continue
        evidence = evidence_for(cand.keyword, snap, site_host)
        text = " ".join(
            f"{r.title} {r.snippet or ''}" for r in snap.data.organic[:TOP_N]
        ).lower()
        gap_hits = [
            g.term for g in gaps if g.kind == "phrase" and cand.normalized in g.queries
        ]
        gap_hits = [t for t in gap_hits if t in normalize_keyword(text)]
        question_hits = [
            g.term
            for g in gaps
            if g.kind == "question" and cand.normalized in g.queries
        ]
        support = len(gap_hits) + len(question_hits)
        paa = evidence.paa_count
        rationale: list[str] = []

        if cand.intent in ("transactional", "navigational"):
            action = "deprioritized"
            rationale.append(
                f"Intent looks {cand.intent} ({cand.intent_basis}); a product or brand page "
                "fits better than a blog article."
            )
        elif evidence.site_position is not None and evidence.site_position <= 3:
            action = "monitor"
            rationale.append(
                f"The site already appears at position {evidence.site_position} in this snapshot."
            )
        elif evidence.site_position is not None:
            action = "improve_existing"
            rationale.append(
                f"The site appears at position {evidence.site_position}; improving that page "
                "is cheaper than writing a new one."
            )
        elif support > 0 or paa >= 1:
            action = "write_new"
            rationale.append("The site does not appear in the top 10 of this snapshot.")
        else:
            action = "low_evidence"
            rationale.append(
                "No gap term or People Also Ask question was observed for this query."
            )
        if gap_hits:
            rationale.append(
                f"Competitor results cover phrases the site's text lacks: {', '.join(gap_hits[:5])}."
            )
        if question_hits:
            rationale.append(
                f"{len(question_hits)} People Also Ask question(s) look unanswered on the site."
            )
        if evidence.ai_overview == "present":
            rationale.append(
                "An AI Overview is present for this query"
                + (
                    " and cites the site."
                    if evidence.ai_overview_cites_site
                    else "; the site is not cited."
                )
            )
        if evidence.featured_snippet_domain:
            rationale.append(
                f"A featured snippet is held by {evidence.featured_snippet_domain}."
            )
        rationale.append(
            "Search volume and difficulty are not available from the current provider."
        )

        impact: Level = (
            "high"
            if support >= 3 or (support >= 1 and paa >= 3)
            else ("medium" if support >= 1 or paa >= 2 else "low")
        )
        confidence: Level = (
            "high"
            if evidence.organic_count >= 8
            else ("medium" if evidence.organic_count >= 4 else "low")
        )
        if cand.intent is None:
            confidence = _lower(confidence)
        effort: Level = "low" if action in ("improve_existing", "monitor") else "medium"
        out.append(
            Opportunity(
                keyword=cand.keyword,
                normalized=cand.normalized,
                intent=cand.intent,
                intent_basis=cand.intent_basis,
                cluster=cand.cluster,
                action=action,
                impact=impact,
                confidence=confidence,
                effort=effort,
                risk="low",
                tier=action,
                rationale=rationale,
                serp=evidence,
                gap_terms=gap_hits,
                questions=question_hits,
                metrics=unavailable_metrics(),
                basis="inferred",
            )
        )

    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(
        key=lambda o: (
            _TIER_ORDER.index(o.tier),
            rank[o.impact],
            rank[o.confidence],
            -(len(o.gap_terms) + len(o.questions)),
            o.keyword,
        )
    )
    for position, opportunity in enumerate(out, start=1):
        opportunity.rank = position
    return out


__all__ = [
    "STOPWORDS",
    "Snapshot",
    "build_opportunities",
    "cluster_keywords",
    "compare_competitors",
    "detect_gaps",
    "evidence_for",
    "find_competitors",
    "site_position",
]
