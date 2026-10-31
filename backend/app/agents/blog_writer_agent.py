"""BlogWriterAgent: generates SEO-focused articles.

Input: an approved ``ContentBrief`` and cited evidence. Output (future): a
``BlogArticle`` in DRAFT status; it must never self-approve for publishing.
"""

from app.agents.base import BaseAgent
from app.schemas.content import BlogArticle, ContentBrief


class BlogWriterAgent(BaseAgent):
    """Draft source-grounded articles."""

    name = "blog_writer_agent"
    responsibility = "Generate SEO-focused articles."
    uses_llm = True

    async def draft(self, brief: ContentBrief) -> BlogArticle:
        """Return a draft article for ``brief``.

        Errors: ``IntegrationException`` on LLM failure.
        """
        # TODO: generate with citations and claim traceability to sources.
        raise self._not_implemented("draft")
