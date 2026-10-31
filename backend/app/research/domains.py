"""Small, dependency-free domain helpers shared by discovery, mapping and crawling."""

import re

# Two-level public suffixes we commonly meet; a full public-suffix list is not
# bundled, so unknown multi-part suffixes fall back to the last two labels.
_TWO_LEVEL_SUFFIXES = frozenset(
    {
        "co.in",
        "org.in",
        "net.in",
        "gov.in",
        "ac.in",
        "co.uk",
        "org.uk",
        "ac.uk",
        "com.au",
        "net.au",
        "co.nz",
        "co.jp",
        "com.br",
        "com.sg",
        "co.za",
    }
)
_LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "gmbh"}
)


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (``www.docs.example.co.in`` -> ``example.co.in``)."""  # noqa: E501
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_label(host: str) -> str:
    """The brand-bearing label of a host (``www.acme.co.in`` -> ``acme``)."""
    return registrable_domain(host).split(".")[0]


def same_site(host: str, base_host: str) -> bool:
    """True if ``host`` is ``base_host`` or a subdomain of its registrable domain."""
    return registrable_domain(host) == registrable_domain(base_host)


def normalize_name(name: str) -> str:
    """Lower-case alphanumeric form of a company/product name without legal suffixes."""
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    tokens = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return "".join(tokens)


def name_tokens(text: str) -> list[str]:
    """Alphanumeric tokens of ``text`` (lower-case), excluding legal suffixes."""
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _LEGAL_SUFFIXES
    ]
