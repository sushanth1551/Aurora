"""Document processing for retrieval indexing."""


class DocumentProcessor:
    """Clean and chunk source documents, preserving source identifiers."""

    async def process(self, source_id: str, content: str) -> list[str]:
        """Return indexable chunks for a source document."""
        # TODO: normalize content, strip boilerplate, then chunk_text().
        raise NotImplementedError("Document processing is not implemented.")
