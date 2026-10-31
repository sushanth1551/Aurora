"""Graph errors, mapped onto the existing provider/persistence taxonomy.

Transient graph failures subclass ``ProviderUnavailableError`` so the shared
``retry_async`` helper retries them; permanent ones do not retry. Messages never
contain credentials, Cypher text or query parameters.
"""

from app.core.exceptions import ConfigurationException, ResearchException
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRequestError,
    ProviderUnavailableError,
)

PROVIDER = "neo4j"


class GraphNotConfiguredError(ConfigurationException):
    """Neo4j credentials are not set; graph operations are unavailable (503)."""

    code = "graph_not_configured"


class GraphUnavailableError(ProviderUnavailableError):
    """Neo4j cannot be reached, the database is unavailable, or a transient error
    occurred (retryable)."""

    code = "graph_unavailable"

    def __init__(self, message: str = "graph database unavailable") -> None:
        super().__init__(PROVIDER, message)


class GraphAuthenticationError(ProviderAuthenticationError):
    """Neo4j rejected the credentials (not retried)."""

    code = "graph_authentication_error"

    def __init__(self) -> None:
        super().__init__(PROVIDER, "credentials rejected")


class GraphQueryError(ProviderRequestError):
    """A query was rejected by Neo4j (syntax, constraint, type); not retried."""

    code = "graph_query_error"

    def __init__(self, message: str = "graph query rejected") -> None:
        super().__init__(PROVIDER, message)


class GraphValidationError(ResearchException):
    """Input to a graph operation violates the schema (label, type, depth...)."""

    code = "graph_validation_error"


class GraphSyncDisabledError(ConfigurationException):
    """``GRAPH_SYNC_ENABLED`` is false."""

    code = "graph_sync_disabled"


__all__ = [
    "GraphAuthenticationError",
    "GraphNotConfiguredError",
    "GraphQueryError",
    "GraphSyncDisabledError",
    "GraphUnavailableError",
    "GraphValidationError",
    "ProviderError",
]
