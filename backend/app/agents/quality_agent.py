"""QualityAgent: checks factual accuracy, SEO quality, and content quality.

Input: a ``BlogArticle``. Output (future): a ``ContentQualityReport``.
"""

from app.agents.base import BaseAgent
from app.schemas.content import BlogArticle, ContentQualityReport


class QualityAgent(BaseAgent):
    """Audit drafts before human review."""

    name = "quality_agent"
    responsibility = "Check factual accuracy, SEO quality, and content quality."
    uses_llm = True

    async def audit(self, article: BlogArticle) -> ContentQualityReport:
        """Return a quality report.

        Errors: ``IntegrationException`` if a verification provider fails.
        """
        # TODO: deterministic SEO checks via SeoAuditor; claim verification
        # against cited sources; LLM only for judgement-based checks.
        raise self._not_implemented("audit")
