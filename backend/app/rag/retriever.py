"""Vector retrieval boundary (ChromaDB is the planned initial store)."""


class VectorRetriever:
    """Retrieve relevant source passages by semantic similarity."""

    async def search(self, query: str, limit: int = 5) -> list[str]:
        """Return up to ``limit`` passages relevant to ``query``."""
        # TODO: embed the query and query the vector store; return passages
        # with their source identifiers.
        raise NotImplementedError("Vector retrieval is not implemented.")
