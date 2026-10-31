"""SerpAgent: analyzes search engine results.

Input: a search query. Output (future): ``SerpResult`` items plus derived
observations such as result types and dominant intent.
"""

from app.agents.base import BaseAgent
from app.schemas.seo import SeoAnalysisResponse


class SerpAgent(BaseAgent):
    """Interpret search results for a query."""

    name = "serp_agent"
    responsibility = "Analyze search engine results."
    uses_llm = False

    async def analyze(self, query: str) -> SeoAnalysisResponse:
        """Retrieve and analyze results for ``query``.

        Errors: ``ConfigurationException`` if the SERP key is missing,
        ``IntegrationException`` on provider failure.
        """
        # TODO: call SerpApiTool, then deterministic analysis; use an LLM only
        # for intent interpretation if heuristics prove insufficient.
        raise self._not_implemented("analyze")
