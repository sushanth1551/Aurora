"""Deterministic search-intent classification (method: ``seo-toolkit-keyword-research``).

Rules over the query words, the site's brand and, when present, the observed
SERP's features. The result is an *inference* and says which rule produced it;
a query no rule recognises stays ``None`` rather than being guessed. No
numeric confidence is produced.
"""

from app.seo.keywords.models import Intent
from app.seo.keywords.text import tokens
from app.tools.serpapi_features import SerpFeatures

_TRANSACTIONAL = frozenset(
    "buy purchase price prices pricing cost costs cheap discount coupon coupons deal deals order "
    "subscribe trial download quote hire".split()
)
_COMMERCIAL = frozenset(
    "best top vs versus review reviews alternative alternatives compare comparison comparisons "
    "tool tools software platform platforms service services provider providers solution solutions "
    "app apps".split()
)
_INFORMATIONAL = frozenset(
    "how what why when where who which guide tutorial tutorials example examples tips tip meaning "
    "definition learn steps ideas checklist template templates explained explain beginner "
    "beginners".split()
)
_QUESTION_START = frozenset(
    "how what why when where who which can does is are should".split()
)


def classify(
    query: str,
    *,
    brand_tokens: frozenset[str] = frozenset(),
    features: SerpFeatures | None = None,
) -> tuple[Intent | None, str | None]:
    """Return ``(intent, basis)``; ``(None, None)`` when no rule matches."""
    words = tokens(query)
    word_set = set(words)
    if not words:
        return None, None
    if brand_tokens and brand_tokens <= word_set:
        return "navigational", "rule:contains_site_brand"
    if word_set & _TRANSACTIONAL:
        return (
            "transactional",
            f"rule:transactional_term:{sorted(word_set & _TRANSACTIONAL)[0]}",
        )
    if word_set & _COMMERCIAL:
        return "commercial", f"rule:commercial_term:{sorted(word_set & _COMMERCIAL)[0]}"
    if words[0] in _QUESTION_START or word_set & _INFORMATIONAL:
        hit = (
            words[0]
            if words[0] in _QUESTION_START
            else sorted(word_set & _INFORMATIONAL)[0]
        )
        return "informational", f"rule:informational_term:{hit}"
    if features is not None:
        if features.shopping_results:
            return "transactional", "serp_feature:shopping_results"
        if features.people_also_ask or features.featured_snippet:
            return "informational", "serp_feature:question_features"
    return None, None


def brand_tokens_for(host: str) -> frozenset[str]:
    """Brand words from a host, e.g. ``www.acme-tools.com`` -> {acme, tools}; empty if too generic."""
    name = (
        host.lower().removeprefix("www.").rsplit(".", 1)[0]
        if "." in host
        else host.lower()
    )
    parts = frozenset(t for t in tokens(name.replace("-", " ")) if len(t) >= 3)
    return parts
