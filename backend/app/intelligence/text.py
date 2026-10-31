"""Small deterministic text helpers shared by ranking and citation checks."""

import re

STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    their this to was were will with top best companies company india research
    compare comparison identify analysis analyze products product services
    service""".split()
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.-]*")
_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")


def normalize_ws(text: str) -> str:
    """Collapse whitespace and lower-case (for substring checks)."""
    return _WS.sub(" ", text).strip().lower()


def terms(text: str) -> set[str]:
    """Distinctive lower-case terms of ``text`` (no stopwords, length >= 2)."""
    found = {t.strip(".-") for t in _TOKEN.findall(text.lower())}
    return {t for t in found if len(t) >= 2 and t not in STOPWORDS}


def overlap(needles: set[str], haystack: set[str]) -> float:
    """Share of ``needles`` present in ``haystack`` (0 when ``needles`` is empty)."""
    if not needles:
        return 0.0
    return len(needles & haystack) / len(needles)


def contains_excerpt(text: str, excerpt: str) -> bool:
    """Whether ``excerpt`` occurs in ``text`` ignoring whitespace and case."""
    needle = normalize_ws(excerpt)
    return bool(needle) and needle in normalize_ws(text)


def numbers_in(text: str) -> set[str]:
    """Numeric tokens (prices, years, counts, percentages), commas removed."""
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


#: Superlative / ranking wording that comparable evidence almost never supports.
RANKING = re.compile(
    r"\b(best|leading|leader|top[- ]?(?:rated|tier|ranked)?|#\s?1|number one|no\. ?1"
    r"|most popular|market leader|dominant|largest|fastest[- ]growing|outperform\w*"
    r"|superior|better than|worse than)\b",
    re.IGNORECASE,
)


def is_ranking_claim(text: str) -> bool:
    """Whether ``text`` asserts a ranking or superlative (never accepted as a fact)."""
    return bool(RANKING.search(text))
