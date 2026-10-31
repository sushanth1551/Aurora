"""Normalization for graph display/lookup fields and stable graph identifiers.

Two normalized forms exist on purpose:

* ``normalized_name`` (graph property): Unicode NFKC, casefolded, whitespace
  collapsed. Punctuation and meaningful tokens are kept ("C++", "Node.js").
* ``match_key``: Module 3's identity form (``normalize_entity_name``), which is
  more aggressive (strips punctuation, legal suffixes, tech aliases). It is used
  only to decide identity and to derive ids, so graph ids equal Module 3 ids.
"""

import re
import unicodedata
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from app.research.domains import registrable_domain

_WS = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """NFKC + casefold + collapsed whitespace; punctuation preserved."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def normalize_domain(value: str | None) -> str | None:
    """Registrable domain of a host or URL (``https://WWW.Acme.co.in/x`` -> ``acme.co.in``)."""
    if not value:
        return None
    host = urlsplit(value).hostname if "://" in value else value
    host = (host or "").strip().strip(".").lower()
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return registrable_domain(host.removeprefix("www."))


def normalize_url(url: str) -> str:
    """Scheme/host-insensitive key: host without ``www.``, path without trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


def graph_id(kind: str, key: str) -> str:
    """Deterministic id for graph-only nodes (same input -> same id, forever)."""
    return str(uuid5(NAMESPACE_URL, f"aurora:graph:{kind}:{key}"))


def company_id_for_domain(domain: str) -> str:
    return graph_id("company", domain)


def source_id_for_url(url: str) -> str:
    return graph_id("source", normalize_url(url))


def concept_id(kind: str, match_key: str) -> str:
    """Shared concepts with no owning website (integration, topic)."""
    return graph_id(kind, match_key)
