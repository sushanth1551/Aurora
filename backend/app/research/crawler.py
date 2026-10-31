"""Crawler service: safe URL validation and (future) page fetching.

``validate_crawl_url`` is real and deterministic. Fetching is delegated to the
Firecrawl tool and is not implemented yet.
"""

import ipaddress
from urllib.parse import urlsplit

from app.core.exceptions import ResearchException
from app.tools.firecrawl_tool import FirecrawlTool

ALLOWED_SCHEMES = frozenset({"http", "https"})


def validate_crawl_url(url: str) -> str:
    """Return ``url`` if it is a safe http(s) target, else raise.

    Rejects other schemes, missing hosts, ``localhost``, and private, loopback,
    link-local, or reserved IP literals. Hostnames that *resolve* to private
    addresses are not detected here.
    """
    # TODO: resolve DNS and re-check addresses; re-validate every redirect hop.
    parts = urlsplit(url.strip())
    host = parts.hostname
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not host:
        raise ResearchException("Only http(s) URLs with a host can be crawled.")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ResearchException("Crawling local addresses is not allowed.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url.strip()
    if not address.is_global:
        raise ResearchException("Crawling non-public addresses is not allowed.")
    return url.strip()


class Crawler:
    """Fetch page content through an injected crawl provider."""

    def __init__(self, tool: FirecrawlTool | None = None) -> None:
        self._tool = tool or FirecrawlTool()

    async def fetch(self, url: str) -> dict[str, object]:
        """Validate ``url`` and fetch its content with provenance."""
        safe_url = validate_crawl_url(url)
        # TODO: enforce per-domain rate limits and size limits.
        return await self._tool.crawl(safe_url)
