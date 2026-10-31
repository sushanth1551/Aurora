"""SerpApi adapter for search engine result retrieval.

Future integration: call the SerpApi HTTP API with ``httpx`` (timeouts, bounded
result counts) and map organic results to ``app.schemas.seo.SerpResult``.
"""

from app.schemas.seo import SerpResult
from app.tools.base import ExternalTool


class SerpApiTool(ExternalTool):
    """Retrieve search engine results through SerpApi."""

    provider = "SerpApi"
    required_settings = ("serpapi_api_key",)

    async def search(self, query: str, num_results: int = 10) -> list[SerpResult]:
        """Return organic results for ``query``.

        Raises ConfigurationException if the API key is unset.
        """
        self.ensure_configured()
        # TODO: perform the request, validate the response, map provider errors
        # to IntegrationException, never log the API key or full request URL.
        raise self._not_implemented("search")
