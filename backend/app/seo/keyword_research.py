"""Keyword research service (deterministic logic over provider data)."""

from app.schemas.seo import KeywordInfo, KeywordResearchRequest


class KeywordResearchService:
    """Collect and rank keywords from configured data sources."""

    async def research(self, request: KeywordResearchRequest) -> list[KeywordInfo]:
        """Return ranked keywords for the request."""
        # TODO: integrate a keyword data provider; never fabricate metrics.
        raise NotImplementedError("Keyword research is not implemented.")
