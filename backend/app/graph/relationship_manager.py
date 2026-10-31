"""Relationship persistence for the knowledge graph."""


class RelationshipManager:
    """Create provenance-aware relationships between entities."""

    async def create_relationship(
        self, source_id: str, target_id: str, relationship_type: str
    ) -> None:
        """Create a relationship; ``relationship_type`` must be in the schema."""
        # TODO: validate against graph_schema.RELATIONSHIP_TYPES and run a
        # parameterized Cypher MERGE through Neo4jClient.
        raise NotImplementedError("Relationship management is not implemented.")
