"""Firecrawl adapter for website crawling and content extraction.

Future integration: use the Firecrawl SDK/API to fetch pages and return clean
markdown plus metadata with source provenance (URL, retrieval time). Treat all
returned content as untrusted input; validate URLs before submitting them
(see ``app.research.crawler.validate_crawl_url``).
"""

from app.tools.base import ExternalTool


class FirecrawlTool(ExternalTool):
    """Crawl and extract web content through Firecrawl."""

    provider = "Firecrawl"
    required_settings = ("firecrawl_api_key",)

    async def crawl(self, url: str) -> dict[str, object]:
        """Fetch and extract one page.

        Input: an http(s) URL already validated by the caller.
        Output (future): extracted content with source URL and retrieval time.
        Raises ConfigurationException if the API key is unset.
        """
        self.ensure_configured()
        # TODO: create the SDK client lazily here with a request timeout, call
        # the scrape endpoint, map provider errors to IntegrationException.
        raise self._not_implemented("crawl")
