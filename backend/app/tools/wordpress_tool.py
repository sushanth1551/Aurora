"""WordPress REST API adapter.

Future integration: create draft posts through the WordPress REST API using an
application password. This adapter only transports content; the decision to
publish belongs to ``app.publishing`` and requires explicit human approval.
"""

from app.tools.base import ExternalTool


class WordPressTool(ExternalTool):
    """Create and update posts on a WordPress site."""

    provider = "WordPress"
    required_settings = (
        "wordpress_url",
        "wordpress_username",
        "wordpress_application_password",
    )

    async def create_draft(self, title: str, content: str) -> int:
        """Create a draft post and return its WordPress id.

        Raises ConfigurationException if credentials are unset.
        """
        self.ensure_configured()
        # TODO: POST to /wp-json/wp/v2/posts with status="draft", enforce HTTPS
        # and timeouts, and map failures to IntegrationException.
        raise self._not_implemented("create_draft")
