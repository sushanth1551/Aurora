"""Text helpers shared by extraction and evidence validation."""

import re

_WS = re.compile(r"\s+")
_MARKUP = re.compile(r"[*_`]+")


def squash(text: str) -> str:
    """Case-fold, drop emphasis markers and collapse whitespace for comparison."""
    return _WS.sub(" ", _MARKUP.sub("", text)).strip().casefold()


def excerpt_in_text(excerpt: str, text: str) -> bool:
    """True if ``excerpt`` occurs in ``text`` ignoring case, emphasis and whitespace."""
    needle = squash(excerpt)
    return bool(needle) and needle in squash(text)


def trim_excerpt(text: str, limit: int = 300) -> str:
    """Shorten an excerpt to ``limit`` characters on a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") or text[:limit]
