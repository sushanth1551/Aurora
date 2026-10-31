"""Neo4j driver lifecycle boundary.

The ``neo4j`` package is imported lazily when connecting is implemented; nothing
connects at import or construction time. All Cypher must be parameterized.
"""

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationException


class Neo4jClient:
    """Own a future Neo4j driver created from injected settings."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        """Whether a Neo4j password has been provided."""
        password = self._settings.neo4j_password
        return password is not None and bool(password.get_secret_value())

    async def connect(self) -> None:
        """Initialize and verify a Neo4j driver."""
        if not self.is_configured:
            raise ConfigurationException("Neo4j is not configured; set NEO4J_PASSWORD.")
        # TODO: create neo4j.AsyncGraphDatabase driver, verify connectivity with
        # a timeout, and expose close() for shutdown.
        raise NotImplementedError("Neo4j client is not implemented.")
