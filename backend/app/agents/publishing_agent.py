"""PublishingAgent: coordinates approved WordPress publishing.

Input: a ``BlogArticle`` with APPROVED status. Output (future): the remote post
identifier. Publishing is an external write and requires human approval.
"""

from app.agents.base import BaseAgent
from app.schemas.content import BlogArticle


class PublishingAgent(BaseAgent):
    """Publish only content a human has explicitly approved."""

    name = "publishing_agent"
    responsibility = "Coordinate approved WordPress publishing."
    uses_llm = False

    async def publish(self, article: BlogArticle) -> str:
        """Publish an approved article and return the remote post id.

        Errors: ``ResearchException``/validation error if not approved;
        ``IntegrationException`` on WordPress failure.
        """
        # TODO: verify ArticleStatus.APPROVED, delegate to WordPressPublisher.
        raise self._not_implemented("publish")
