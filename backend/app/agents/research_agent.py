"""ResearchAgent: discovers and coordinates research sources.

Input: a validated ``ResearchRequest``. Output (future): a list of
``SourceReference`` objects gathered within ``max_sources``.
"""

from app.agents.base import BaseAgent
from app.schemas.research import ResearchRequest, SourceReference


class ResearchAgent(BaseAgent):
    """Find candidate sources and coordinate crawling of approved ones."""

    name = "research_agent"
    responsibility = "Discover and coordinate research sources."
    uses_llm = False

    async def discover_sources(self, request: ResearchRequest) -> list[SourceReference]:
        """Return candidate sources for the request.

        Errors: raise ``ResearchException`` for unusable requests and
        ``IntegrationException`` when a provider fails.
        """
        # TODO: query SERP/search providers via injected tools, deduplicate,
        # validate URLs (scheme/SSRF), and respect request.max_results.
        raise self._not_implemented("discover_sources")
