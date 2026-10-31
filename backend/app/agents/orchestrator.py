"""Orchestrator: coordinates agents and workflows.

Input: a ``ResearchRequest``. Output (future): a workflow run identifier.
Execution will be delegated to LangGraph workflows in ``app.workflows``; this
class must not simulate agent behavior or return fabricated results.
"""

from uuid import UUID

from app.agents.base import BaseAgent
from app.schemas.research import ResearchRequest


class Orchestrator(BaseAgent):
    """Start and track workflow runs across agents."""

    name = "orchestrator"
    responsibility = "Coordinate agents and workflows."
    uses_llm = False

    async def run(self, request: ResearchRequest) -> UUID:
        """Start a workflow run and return its id.

        Errors: ``ResearchException`` for invalid requests.
        """
        # TODO: build the workflow, persist run state, enforce human approval
        # gates before any external write.
        raise self._not_implemented("run")


ResearchOrchestrator = Orchestrator
