"""Source manager: tracks sources and their provenance for a research run."""

from app.schemas.research import SourceReference


class SourceManager:
    """Register, deduplicate, and retrieve sources (persistence not built)."""

    async def register(self, source: SourceReference) -> None:
        """Record a source for a research run."""
        # TODO: persist via ResearchRepository; deduplicate by normalized URL.
        raise NotImplementedError("Source registration is not implemented.")

    async def list_sources(self, research_id: str) -> list[SourceReference]:
        """Return sources recorded for a run."""
        # TODO: read from the repository.
        raise NotImplementedError("Source listing is not implemented.")
