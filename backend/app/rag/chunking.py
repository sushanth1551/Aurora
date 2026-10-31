"""Text chunking boundary for retrieval pipelines."""


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    """Split text into bounded overlapping chunks."""
    # TODO: validate 0 <= overlap < chunk_size, then split on sentence or
    # paragraph boundaries where possible.
    raise NotImplementedError("Text chunking is not implemented.")
