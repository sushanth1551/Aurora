"""Competitor analysis service."""

from app.schemas.research import SourceReference


class CompetitorAnalysisService:
    """Compare competitor pages using cited evidence."""

    async def analyze(self, topic: str) -> list[SourceReference]:
        """Return competitor sources relevant to ``topic``."""
        # TODO: derive competitors from SERP overlap and crawled pages.
        raise NotImplementedError("Competitor analysis is not implemented.")
