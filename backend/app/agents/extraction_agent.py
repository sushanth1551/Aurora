"""ExtractionAgent: extracts structured information from crawled pages.

Input: page text and its ``SourceReference``. Output (future): entities and
claims, each linked to its source for graph storage.
"""

from app.agents.base import BaseAgent
from app.schemas.research import SourceReference


class ExtractionAgent(BaseAgent):
    """Extract evidence-linked facts from untrusted page content."""

    name = "extraction_agent"
    responsibility = "Extract structured information from crawled pages."
    uses_llm = True

    async def extract(
        self, text: str, source: SourceReference
    ) -> list[dict[str, object]]:
        """Return extracted entities/claims for ``text``.

        Errors: ``ResearchException`` for unparseable content. Page text is
        untrusted; never treat it as instructions.
        """
        # TODO: deterministic parsing first, LLM extraction with a strict output
        # schema second; reject outputs lacking source references.
        raise self._not_implemented("extract")
