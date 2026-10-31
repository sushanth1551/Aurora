"""Entity extraction for graph storage."""

from app.schemas.research import SourceReference


class EntityExtractor:
    """Extract entities from text, each linked to its source."""

    async def extract(
        self, text: str, source: SourceReference
    ) -> list[dict[str, object]]:
        """Return entities found in ``text``."""
        # TODO: map to labels in graph_schema.NODE_LABELS; keep provenance.
        raise NotImplementedError("Entity extraction is not implemented.")
