"""Embedding provider boundary."""


class EmbeddingService:
    """Generate vectors through an explicitly configured embedding provider."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per input text."""
        # TODO: choose a provider, enforce batch/size limits and privacy rules.
        raise NotImplementedError("Embedding generation is not implemented.")
