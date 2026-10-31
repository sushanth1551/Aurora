"""LangGraph workflow skeleton for multi-step research.

``langgraph`` is a declared dependency (requirements.txt) but is imported only
inside the builder so this module imports safely and needs no API keys. No
workflow is built or executed yet.

Future implementation points:
- State: ``ResearchState`` below, extended with reducers for list fields.
- Nodes: discover sources -> crawl -> extract -> store -> synthesize.
- Edges / conditional routing: loop while sources remain and budget allows;
  route to an error node on unrecoverable failures.
- Retry: bounded retries with backoff for provider (IntegrationException) errors.
- Error handling: collect errors in state; never swallow them silently.
- Human approval: interrupt before any external write.
- Execution and checkpointing: compile with a persistent checkpointer
  (PostgreSQL) so runs can resume; expose run status via the API.
"""

from typing import TypedDict


class ResearchState(TypedDict, total=False):
    """Data passed between research workflow nodes."""

    topic: str
    max_sources: int
    sources: list[str]
    findings: list[str]
    errors: list[str]


def build_research_workflow() -> None:
    """Compose research nodes into a compiled LangGraph graph (not yet built)."""
    # TODO: from langgraph.graph import StateGraph  # import lazily here
    raise NotImplementedError("Research workflow is not implemented.")
