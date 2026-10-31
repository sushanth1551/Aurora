"""Readiness checks: database reachable, migrations at head, graph status.

Used by ``GET /health/ready`` and the Windows start script. Every check has a
timeout and reports a status word only: no URLs, credentials, versions of
secrets or raw error text. Nothing here runs at import time.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import PROJECT_ROOT
from app.core.exceptions import ConfigurationException
from app.graph.graph_health import GraphStatus, check_health
from app.graph.neo4j_client import Neo4jClient

if TYPE_CHECKING:
    from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)

EngineFactory = Callable[[], AsyncEngine]
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"
CHECK_TIMEOUT_SECONDS = 5.0

DatabaseState = Literal["ok", "unavailable", "not_configured"]
MigrationState = Literal["current", "behind", "ahead_or_unknown", "unknown"]


class DatabaseReadiness(BaseModel):
    status: DatabaseState


class MigrationReadiness(BaseModel):
    status: MigrationState
    current: str | None = None
    head: str | None = None
    pending: list[str] = []


class GraphReadiness(BaseModel):
    status: str
    required: bool = False


class ReadinessReport(BaseModel):
    """``ready``: usable; ``degraded``: usable with gaps; ``not_ready``: no database."""

    status: Literal["ready", "degraded", "not_ready"]
    database: DatabaseReadiness
    migrations: MigrationReadiness
    graph: GraphReadiness


def migration_heads(
    migrations_dir: Path = MIGRATIONS_DIR,
) -> tuple[list[str], "ScriptDirectory"]:
    """Alembic heads and the script directory (read from files; no database)."""
    from alembic.script import ScriptDirectory  # local import: tooling dependency

    script = ScriptDirectory(str(migrations_dir))
    return list(script.get_heads()), script


async def _current_revision(engine: AsyncEngine) -> tuple[DatabaseState, str | None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            try:
                row = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).first()
            except Exception:  # table missing: database never migrated
                return "ok", None
            return "ok", (row[0] if row else None)
    except ConfigurationException:
        return "not_configured", None
    except Exception as exc:  # noqa: BLE001 - report a status, never the error text
        logger.warning("readiness_database_unavailable error=%s", type(exc).__name__)
        return "unavailable", None


def _migration_state(
    current: str | None, heads: list[str], script: "ScriptDirectory"
) -> MigrationReadiness:
    head = heads[0] if len(heads) == 1 else None
    if current is not None and current in heads:
        return MigrationReadiness(status="current", current=current, head=head)
    try:
        pending = [
            rev.revision for rev in script.iterate_revisions("heads", current or "base")
        ]
    except Exception:  # unknown revision in the database (e.g. a newer branch)
        return MigrationReadiness(status="ahead_or_unknown", current=current, head=head)
    return MigrationReadiness(
        status="behind", current=current, head=head, pending=list(reversed(pending))
    )


async def check_readiness(
    engine_factory: EngineFactory,
    graph_client: Neo4jClient,
    migrations_dir: Path = MIGRATIONS_DIR,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> ReadinessReport:
    """Run all checks concurrently, each bounded by ``timeout``."""
    try:
        engine = engine_factory()
    except ConfigurationException:
        engine = None

    async def database() -> tuple[DatabaseState, str | None]:
        if engine is None:
            return "not_configured", None
        try:
            return await asyncio.wait_for(_current_revision(engine), timeout)
        except TimeoutError:
            return "unavailable", None

    async def graph() -> str:
        try:
            health = await asyncio.wait_for(check_health(graph_client), timeout)
            return str(health.status.value)
        except TimeoutError:
            return GraphStatus.UNAVAILABLE.value

    (db_state, current), graph_state = await asyncio.gather(database(), graph())
    try:
        heads, script = migration_heads(migrations_dir)
        migrations = (
            _migration_state(current, heads, script)
            if db_state == "ok"
            else MigrationReadiness(status="unknown", head=heads[0] if heads else None)
        )
    except Exception as exc:  # noqa: BLE001 - migration files unreadable
        logger.warning("readiness_migrations_unreadable error=%s", type(exc).__name__)
        migrations = MigrationReadiness(status="unknown")

    graph_ok = graph_state in (
        GraphStatus.CONNECTED.value,
        GraphStatus.NOT_CONFIGURED.value,
    )
    if db_state != "ok":
        status: Literal["ready", "degraded", "not_ready"] = "not_ready"
    elif migrations.status != "current" or not graph_ok:
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(
        status=status,
        database=DatabaseReadiness(status=db_state),
        migrations=migrations,
        graph=GraphReadiness(status=graph_state),
    )
