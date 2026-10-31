"""LangGraph workflow skeleton for SEO strategy and content creation.

``langgraph`` is imported lazily inside the builder; this module needs no API
keys and runs nothing on import.

Future implementation points:
- State: ``SeoContentState`` below.
- Nodes: keyword research -> SERP analysis -> strategy -> brief -> draft ->
  quality audit -> human approval -> publish.
- Conditional routing: send failed quality audits back to drafting with a
  bounded revision count.
- Retry / error handling: bounded retries for provider errors; errors recorded
  in state.
- Human approval: mandatory interrupt before the publish node; publishing must
  not occur without explicit approval.
- Execution and checkpointing: persistent checkpointer for resumable runs.
"""

from typing import TypedDict


class SeoContentState(TypedDict, total=False):
    """Data passed between SEO content workflow nodes."""

    topic: str
    keywords: list[str]
    brief: str
    draft: str
    audit_findings: list[str]
    approved: bool
    errors: list[str]


def build_seo_content_workflow() -> None:
    """Compose the SEO content graph (not yet built)."""
    # TODO: from langgraph.graph import StateGraph  # import lazily here
    raise NotImplementedError("SEO content workflow is not implemented.")
