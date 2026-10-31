"""KeywordAgent: performs keyword research.

Input: ``KeywordResearchRequest``. Output (future): ``KeywordInfo`` items whose
metrics come only from a configured data provider.
"""

from app.agents.base import BaseAgent
from app.schemas.seo import KeywordInfo, KeywordResearchRequest


class KeywordAgent(BaseAgent):
    """Discover and prioritize keywords."""

    name = "keyword_agent"
    responsibility = "Perform keyword research."
    uses_llm = False

    async def research(self, request: KeywordResearchRequest) -> list[KeywordInfo]:
        """Return keyword opportunities for the request.

        Errors: ``IntegrationException`` on provider failure. Never invent
        search-volume or difficulty numbers.
        """
        # TODO: use provider data via KeywordResearchService; clustering and
        # prioritization should be deterministic.
        raise self._not_implemented("research")
