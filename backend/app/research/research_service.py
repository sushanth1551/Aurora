"""Application service boundary for research requests."""

from uuid import UUID

from app.schemas.research import ResearchRequest


class ResearchService:
    """Coordinate research dependencies through injected collaborators."""

    async def submit(self, request: ResearchRequest) -> UUID:
        """Start a research run and return its id."""
        # TODO: persist the request, start the research workflow, and track
        # its lifecycle. The API route does not call this yet.
        raise NotImplementedError("Research service is not implemented.")
