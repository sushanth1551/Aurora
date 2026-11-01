"""Keyword/text normalization and tokenization shared by Phase 3 analyzers."""

import re
import unicodedata
from urllib.parse import urlsplit

_PUNCT = re.compile(r"[^\w\s'-]", re.UNICODE)
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*")

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how i in is it its of on or that the this to was what
    when where which who why will with you your we our they their can do does did not no yes if then than so
    about into over under up down out more most less best top new get make use using used also just very
    may might should could would one two three""".split()
)
BOILERPLATE = frozenset(
    "cookie cookies privacy terms conditions login log-in signin sign-in signup sign-up contact "
    "subscribe newsletter menu skip footer copyright home".split()
)


def normalize_keyword(text: str) -> str:
    """NFKC, casefold, strip punctuation (keeps apostrophes/hyphens), collapse spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return _WS.sub(" ", _PUNCT.sub(" ", text)).strip()


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(normalize_keyword(text))


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) >= 3]


def bigrams(text: str) -> set[str]:
    """Adjacent content-word pairs, e.g. 'email marketing'. Stopwords break a pair."""
    out: set[str] = set()
    previous: str | None = None
    for token in tokens(text):
        if token in STOPWORDS or len(token) < 3:
            previous = None
            continue
        if previous is not None:
            out.add(f"{previous} {token}")
        previous = token
    return out


def comparable_url(url: str) -> str:
    """Scheme-, www-, fragment- and trailing-slash-insensitive form for overlap checks."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def bare_host(host: str) -> str:
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host
