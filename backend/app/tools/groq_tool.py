"""Groq adapter for fast LLM inference.

Future integration: use the Groq SDK (or ``langchain-groq``) for low-latency,
low-cost steps such as classification or summarization. Create the client
lazily from injected settings, with timeouts and token limits.
"""

from app.tools.base import ExternalTool


class GroqTool(ExternalTool):
    """Run LLM completions through Groq."""

    provider = "Groq"
    required_settings = ("groq_api_key",)

    async def complete(self, prompt: str) -> str:
        """Return a completion for ``prompt``.

        Raises ConfigurationException if the API key is unset.
        """
        self.ensure_configured()
        # TODO: call the Groq chat API; treat prompt content as sensitive and
        # do not log it; map provider errors to IntegrationException.
        raise self._not_implemented("complete")
