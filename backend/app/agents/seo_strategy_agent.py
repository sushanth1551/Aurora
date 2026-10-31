"""SEOStrategyAgent: develops SEO strategies and content recommendations.

Input: keywords and SERP analysis. Output (future): ``ContentBrief`` items.
"""

from app.agents.base import BaseAgent
from app.schemas.content import ContentBrief
from app.schemas.seo import KeywordInfo, SeoAnalysisResponse


class SeoStrategyAgent(BaseAgent):
    """Turn keyword and SERP evidence into content recommendations."""

    name = "seo_strategy_agent"
    responsibility = "Develop SEO strategies and content recommendations."
    uses_llm = True

    async def create_strategy(
        self, keywords: list[KeywordInfo], serp: SeoAnalysisResponse
    ) -> list[ContentBrief]:
        """Return recommended briefs.

        Errors: ``ResearchException`` when inputs are insufficient.
        """
        # TODO: prioritize deterministically, then use an LLM to draft
        # rationale grounded only in the supplied evidence.
        raise self._not_implemented("create_strategy")
