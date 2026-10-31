"""SERP analysis service (deterministic logic over search results)."""

from app.schemas.seo import SeoAnalysisResponse


class SerpAnalysisService:
    """Analyze search result pages for a query."""

    async def analyze(self, query: str) -> SeoAnalysisResponse:
        """Return SERP analysis for ``query``."""
        # TODO: fetch results through SerpApiTool and derive features.
        raise NotImplementedError("SERP analysis is not implemented.")
