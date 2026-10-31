"""Anthropic Claude adapter.

Future integration: use the Anthropic SDK (or ``langchain-anthropic``) for
synthesis and writing steps that need a strong model. Create the client lazily
from injected settings; set timeouts, token limits, and the model id via
configuration rather than hardcoding it here.
"""

from app.tools.base import ExternalTool


class ClaudeTool(ExternalTool):
    """Run LLM completions through Anthropic Claude."""

    provider = "Anthropic Claude"
    required_settings = ("anthropic_api_key",)

    async def complete(self, prompt: str, system: str | None = None) -> str:
        """Return a completion for ``prompt``.

        Raises ConfigurationException if the API key is unset.
        """
        self.ensure_configured()
        # TODO: call the Messages API; defend against prompt injection from
        # retrieved content; map provider errors to IntegrationException.
        raise self._not_implemented("complete")
