"""WordPress publication service boundary.

Publishing is an external write: it must only act on content a human has
explicitly approved. No publishing is implemented.
"""

from app.schemas.content import BlogArticle


class WordPressPublisher:
    """Publish approved articles through an injected WordPress adapter."""

    async def publish(self, article: BlogArticle) -> str:
        """Publish an APPROVED article idempotently and return the post id."""
        # TODO: reject non-approved articles, then call WordPressTool.
        raise NotImplementedError("WordPress publishing is not implemented.")
