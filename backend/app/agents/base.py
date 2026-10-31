"""Shared base class for agent roles.

Agents are thin coordinators. Deterministic work (HTTP, parsing, scoring) belongs
in plain services under ``app.research``, ``app.seo``, etc.; only steps that
genuinely need language-model reasoning should call an LLM tool. Collaborators
are injected, never created at import time.
"""


class BaseAgent:
    """Common metadata and error helpers for agents."""

    #: Short identifier used in logs and workflow node names.
    name: str = "agent"
    #: One-sentence description of the agent's responsibility.
    responsibility: str = ""
    #: Whether the finished agent is expected to call an LLM.
    uses_llm: bool = False

    def _not_implemented(self, operation: str) -> NotImplementedError:
        return NotImplementedError(f"{self.name}: {operation} is not implemented yet.")
