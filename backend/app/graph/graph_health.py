"""Graph health: only reports ``connected`` after a verified round trip."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.graph.graph_exceptions import (
    GraphAuthenticationError,
    GraphNotConfiguredError,
    GraphUnavailableError,
)
from app.graph.neo4j_client import Neo4jClient


class GraphStatus(StrEnum):
    CONNECTED = "connected"  # connectivity, auth and a query verified just now
    DISCONNECTED = "disconnected"  # configured, but no driver has been opened
    UNAVAILABLE = "unavailable"  # configured, but the check failed
    NOT_CONFIGURED = "not_configured"  # credentials missing


class GraphHealth(BaseModel):
    status: GraphStatus
    database: str | None = None
    uri: str | None = None
    detail: str | None = None
    server: dict[str, Any] = Field(default_factory=dict)
    schema_version: str | None = None


async def check_health(client: Neo4jClient, *, probe: bool = True) -> GraphHealth:
    """``probe=False`` reports the passive state without opening a connection."""
    cfg = client.config
    base: dict[str, Any] = {"database": cfg.database, "uri": cfg.uri}
    if not client.is_configured:
        return GraphHealth(
            status=GraphStatus.NOT_CONFIGURED, detail="set NEO4J_PASSWORD", **base
        )
    if not probe:
        status = (
            GraphStatus.DISCONNECTED
            if not client.is_connected
            else GraphStatus.UNAVAILABLE
        )
        return GraphHealth(status=status, detail="not verified (passive check)", **base)
    try:
        await client.verify()
        server = await client.server_info()
        rows = await client.execute_read(
            "MATCH (v:GraphSchemaVersion {id: 'aurora'}) RETURN v.version AS version",
            {},
        )
    except GraphNotConfiguredError:
        return GraphHealth(status=GraphStatus.NOT_CONFIGURED, **base)
    except GraphAuthenticationError:
        return GraphHealth(
            status=GraphStatus.UNAVAILABLE, detail="authentication failed", **base
        )
    except GraphUnavailableError as exc:
        return GraphHealth(status=GraphStatus.UNAVAILABLE, detail=exc.message, **base)
    except Exception as exc:  # noqa: BLE001 - never report healthy on an unknown error
        return GraphHealth(
            status=GraphStatus.UNAVAILABLE, detail=type(exc).__name__, **base
        )
    version = rows[0]["version"] if rows else None
    return GraphHealth(
        status=GraphStatus.CONNECTED, server=server, schema_version=version, **base
    )
