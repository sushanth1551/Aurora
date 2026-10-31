"""Persistence boundary for research jobs and their state."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ResearchRepository:
    """Store and retrieve research job records using an injected session."""

    def __init__(self, session: "AsyncSession | None" = None) -> None:
        self._session = session

    async def get(self, research_id: str) -> None:
        """Fetch a research record by its stable identifier."""
        # TODO: parameterized query; raise ResourceNotFoundException if absent.
        raise NotImplementedError("Research repository is not implemented.")
