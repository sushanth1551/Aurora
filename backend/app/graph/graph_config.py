"""Neo4j connection settings derived from the central ``Settings``.

The password is kept as ``SecretStr`` and only unwrapped when the driver is
created; it never appears in ``repr`` or logs.
"""

from dataclasses import dataclass

from pydantic import SecretStr

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class GraphConfig:
    uri: str
    username: str
    password: SecretStr | None
    database: str
    connection_timeout: float
    query_timeout: float
    max_pool_size: int

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "GraphConfig":
        s = settings or get_settings()
        return cls(
            uri=s.neo4j_uri,
            username=s.neo4j_username,
            password=s.neo4j_password,
            database=s.neo4j_database,
            connection_timeout=s.neo4j_connection_timeout_seconds,
            query_timeout=s.neo4j_query_timeout_seconds,
            max_pool_size=s.neo4j_max_connection_pool_size,
        )

    @property
    def is_configured(self) -> bool:
        """A URI, user and non-empty password are all required."""
        return bool(
            self.uri
            and self.username
            and self.password is not None
            and self.password.get_secret_value()
        )

    def __repr__(self) -> str:  # never show the password
        return (
            f"GraphConfig(uri={self.uri!r}, username={self.username!r}, "
            f"database={self.database!r}, password={'***' if self.password else None})"
        )
