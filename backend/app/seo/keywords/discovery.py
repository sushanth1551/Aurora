"""Keyword candidate discovery from seeds, site topics and observed SERP features.

Sources, in priority order: user seeds, site topics (titles/headings from a
stored technical audit), People Also Ask and related searches observed on
fetched SERPs, and (opt-in) template expansions of the seeds. Candidates are
*queries to consider*, never measured demand. Every candidate records where it
came from; no metric is attached.
"""

from collections.abc import Iterable

from app.seo.keywords.models import Candidate, Source
from app.seo.keywords.text import BOILERPLATE, normalize_keyword, tokens
from app.tools.serpapi_features import SerpFeatures

TEMPLATES = (
    "best {} tools",
    "how to {}",
    "{} vs",
    "{} pricing",
    "{} alternatives",
    "{} examples",
)
MIN_WORDS = 2
MAX_WORDS = 7
MAX_CHARS = 120


def looks_like_query(text: str) -> bool:
    """A short natural phrase, not navigation, legal or boilerplate text."""
    words = tokens(text)
    if not MIN_WORDS <= len(words) <= MAX_WORDS or len(text) > MAX_CHARS:
        return False
    if any(w in BOILERPLATE for w in words):
        return False
    return sum(1 for w in words if w.isalpha()) / len(words) >= 0.6


class CandidatePool:
    """Deduplicating, capped collection preserving first-seen source."""

    def __init__(self, max_candidates: int) -> None:
        self._max = max_candidates
        self._items: dict[str, Candidate] = {}

    def add(
        self, keyword: str, source: Source, detail: str | None = None
    ) -> Candidate | None:
        text = " ".join(keyword.split())[:MAX_CHARS]
        normalized = normalize_keyword(text)
        if not normalized:
            return None
        existing = self._items.get(normalized)
        if existing is not None:
            return existing
        if len(self._items) >= self._max:
            return None
        candidate = Candidate(
            keyword=text, normalized=normalized, source=source, source_detail=detail
        )
        self._items[normalized] = candidate
        return candidate

    def add_many(
        self, keywords: Iterable[str], source: Source, detail: str | None = None
    ) -> int:
        return sum(1 for k in keywords if self.add(k, source, detail) is not None)

    def get(self, normalized: str) -> Candidate | None:
        return self._items.get(normalized)

    @property
    def full(self) -> bool:
        return len(self._items) >= self._max

    def all(self) -> list[Candidate]:
        return list(self._items.values())


def site_topic_phrases(texts: Iterable[str], limit: int) -> list[str]:
    """Deduplicated query-like phrases from page titles and headings, in order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in texts:
        for part in (p.strip() for p in raw.replace("|", "-").split(" - ")):
            normalized = normalize_keyword(part)
            if normalized and normalized not in seen and looks_like_query(part):
                seen.add(normalized)
                out.append(part)
                if len(out) >= limit:
                    return out
    return out


def template_expansions(seed: str) -> list[str]:
    return [t.format(seed) for t in TEMPLATES]


def serp_discoveries(query: str, features: SerpFeatures) -> list[tuple[str, Source]]:
    """Questions and related searches observed for ``query`` (provider-supplied, not invented)."""
    found: list[tuple[str, Source]] = [
        (item.question, "serp_paa") for item in features.people_also_ask
    ]
    found += [(text, "serp_related") for text in features.related_searches]
    return [
        (text, source)
        for text, source in found
        if looks_like_query(text) or source == "serp_paa"
    ]
