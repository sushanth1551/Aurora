"""CompetitorAgent: identifies and analyzes competitor websites.

Input: a company, domain, or topic. Output (future): competitor domains with
evidence-linked observations (``SourceReference`` for each claim).
"""

from app.agents.base import BaseAgent
from app.schemas.research import SourceReference


class CompetitorAgent(BaseAgent):
    """Identify competitors and compare them using cited evidence."""

    name = "competitor_agent"
    responsibility = "Identify and analyze competitor websites."
    uses_llm = True

    async def identify(self, subject: str) -> list[SourceReference]:
        """Return competitor pages relevant to ``subject``.

        Errors: ``ResearchException`` if no evidence is found;
        ``IntegrationException`` on provider failure.
        """
        # TODO: derive competitors from SERP overlap; summarize with an LLM
        # only from retrieved evidence, preserving provenance.
        raise self._not_implemented("identify")
