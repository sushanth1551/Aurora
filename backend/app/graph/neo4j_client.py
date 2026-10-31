"""Neo4j driver lifecycle and safe query execution (official async driver).

* Nothing connects at import or construction; the driver is created on the
  first query or health check and closed with ``close()`` (called on app
  shutdown).
* Every query runs inside a managed read or write transaction
  (``execute_read`` / ``execute_write``) wrapped in ``neo4j.Query`` with the
  configured timeout, so the driver retries transient errors within the
  transaction and the server enforces the time limit.
* All Cypher is parameterized; callers pass static query text built only from
  allowlisted labels (see ``graph_schema``). Driver errors are mapped onto
  ``graph_exceptions`` without exposing messages, queries or parameters.
"""

import logging
from collections.abc import Callable
from typing import Any, LiteralString, cast

from app.core.config import Settings
from app.core.logging import log_event
from app.graph.graph_config import GraphConfig
from app.graph.graph_exceptions import (
    GraphAuthenticationError,
    GraphNotConfiguredError,
    GraphQueryError,
    GraphUnavailableError,
)

logger = logging.getLogger(__name__)

Rows = list[dict[str, Any]]


def map_driver_error(exc: Exception) -> Exception:
    """Translate a ``neo4j`` exception into AURORA's taxonomy (no message leak)."""
    import neo4j.exceptions as ne

    if isinstance(exc, ne.AuthError):
        return GraphAuthenticationError()
    if isinstance(
        exc,
        ne.ServiceUnavailable
        | ne.SessionExpired
        | ne.DatabaseUnavailable
        | ne.TransientError,
    ):
        return GraphUnavailableError(
            f"graph database unavailable ({type(exc).__name__})"
        )
    if isinstance(exc, ne.Neo4jError):
        code = getattr(exc, "code", None) or type(exc).__name__
        if "DatabaseNotFound" in str(code):
            return GraphUnavailableError("configured Neo4j database does not exist")
        return GraphQueryError(f"graph query rejected ({code})")
    if isinstance(exc, ne.DriverError):
        return GraphUnavailableError(f"graph driver error ({type(exc).__name__})")
    if isinstance(exc, TimeoutError | OSError):
        return GraphUnavailableError(f"graph connection failed ({type(exc).__name__})")
    return exc


class Neo4jClient:
    """Owns one ``AsyncDriver`` built from injected settings."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        config: GraphConfig | None = None,
        driver_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config or GraphConfig.from_settings(settings)
        self._driver_factory = driver_factory
        self._driver: Any = None

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    @property
    def is_connected(self) -> bool:
        """Whether a driver object exists (not a health guarantee)."""
        return self._driver is not None

    def _get_driver(self) -> Any:
        if not self.is_configured:
            raise GraphNotConfiguredError(
                "Neo4j is not configured; set NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD."
            )
        if self._driver is None:
            factory = self._driver_factory
            if factory is None:
                from neo4j import AsyncGraphDatabase  # lazy: no import-time cost

                factory = AsyncGraphDatabase.driver
            assert self.config.password is not None
            try:
                self._driver = factory(
                    self.config.uri,
                    auth=(
                        self.config.username,
                        self.config.password.get_secret_value(),
                    ),
                    connection_timeout=self.config.connection_timeout,
                    connection_acquisition_timeout=self.config.connection_timeout,
                    max_connection_pool_size=self.config.max_pool_size,
                    max_transaction_retry_time=self.config.query_timeout,
                )
            except Exception as exc:  # noqa: BLE001 - invalid URI scheme etc.
                mapped = map_driver_error(exc)
                if mapped is exc:
                    raise GraphUnavailableError(
                        "invalid Neo4j connection settings"
                    ) from None
                raise mapped from None
            log_event(
                logger,
                "graph_driver_created",
                uri=self.config.uri,
                database=self.config.database,
            )
        return self._driver

    async def verify(self) -> None:
        """Check connectivity and authentication against the configured database."""
        driver = self._get_driver()
        try:
            await driver.verify_connectivity()
            await self.execute_read("RETURN 1 AS ok", {})
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise map_driver_error(exc) from None

    async def server_info(self) -> dict[str, Any]:
        driver = self._get_driver()
        try:
            info = await driver.get_server_info()
        except Exception as exc:  # noqa: BLE001
            raise map_driver_error(exc) from None
        return {
            "agent": getattr(info, "agent", None),
            "protocol_version": str(getattr(info, "protocol_version", "")),
        }

    async def execute_read(self, query: str, parameters: dict[str, Any]) -> Rows:
        return await self._execute(query, parameters, write=False)

    async def execute_write(self, query: str, parameters: dict[str, Any]) -> Rows:
        return await self._execute(query, parameters, write=True)

    async def _execute(
        self, query: str, parameters: dict[str, Any], *, write: bool
    ) -> Rows:
        from neo4j import Query

        driver = self._get_driver()
        q = Query(cast(LiteralString, query), timeout=self.config.query_timeout)

        async def work(tx: Any) -> Rows:
            result = await tx.run(q, parameters)
            return [record.data() for record in await result.fetch(1_000_000)]

        try:
            async with driver.session(database=self.config.database) as session:
                if write:
                    return await session.execute_write(work)
                return await session.execute_read(work)
        except Exception as exc:  # noqa: BLE001 - mapped below
            mapped = map_driver_error(exc)
            if mapped is exc:
                raise
            log_event(
                logger,
                "graph_query_failed",
                level=logging.WARNING,
                error=getattr(mapped, "code", "error"),
                write=write,
            )
            raise mapped from None

    async def close(self) -> None:
        if self._driver is not None:
            try:
                await self._driver.close()
            finally:
                self._driver = None
                log_event(logger, "graph_driver_closed")


_shared: Neo4jClient | None = None


def get_graph_client() -> Neo4jClient:
    """Process-wide client (created lazily; the driver connects on first use)."""
    global _shared
    if _shared is None:
        _shared = Neo4jClient()
    return _shared


async def close_graph_client() -> None:
    """Close the shared driver if one was ever created (app shutdown)."""
    global _shared
    if _shared is not None:
        await _shared.close()
        _shared = None
