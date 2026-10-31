"""Graph-based retrieval boundary."""


class GraphRetriever:
    """Retrieve context by traversing knowledge-graph relationships."""

    async def search(self, query: str) -> list[str]:
        """Return graph-derived context for ``query``."""
        # TODO: run parameterized Cypher through Neo4jClient and return context
        # with provenance.
        raise NotImplementedError("Graph retrieval is not implemented.")
