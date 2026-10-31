"""Google Search Console adapter.

Future integration: use OAuth credentials (client id/secret and refresh token)
to read search performance for properties the user owns. Request read-only
scopes only.
"""

from app.tools.base import ExternalTool


class SearchConsoleTool(ExternalTool):
    """Read search performance data from Google Search Console."""

    provider = "Google Search Console"
    required_settings = (
        "google_client_id",
        "google_client_secret",
        "google_refresh_token",
    )

    async def query_performance(
        self, property_id: str, start_date: str, end_date: str
    ) -> list[dict[str, object]]:
        """Return performance rows for a property and ISO date range.

        Raises ConfigurationException if credentials are unset.
        """
        self.ensure_configured()
        # TODO: exchange the refresh token, call the searchanalytics query API,
        # and map failures to IntegrationException.
        raise self._not_implemented("query_performance")
