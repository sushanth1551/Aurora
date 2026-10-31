"""Content brief service."""

from app.schemas.content import ContentBrief


class ContentBriefService:
    """Build content briefs from keyword and SERP evidence."""

    async def create(self, brief: ContentBrief) -> ContentBrief:
        """Validate, enrich, and return a content brief."""
        # TODO: enrich the outline from SERP and research evidence.
        raise NotImplementedError("Content brief creation is not implemented.")
