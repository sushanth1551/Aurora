"""Readiness checks (database, migrations, graph) without real services."""

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.api.routes.health import get_engine_factory
from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.core.readiness import check_readiness, migration_heads
from app.graph.neo4j_client import Neo4jClient
from app.main import create_app

HEAD = migration_heads()[0][0]


@pytest.fixture
async def sqlite_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()


async def _stamp(engine: AsyncEngine, revision: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        await conn.execute(
            text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision}
        )


def _no_graph() -> Neo4jClient:
    return Neo4jClient(Settings())  # no password: not_configured, never connects


async def test_ready_when_database_at_head(sqlite_engine: AsyncEngine) -> None:
    await _stamp(sqlite_engine, HEAD)
    report = await check_readiness(lambda: sqlite_engine, _no_graph())
    assert report.status == "ready"
    assert report.migrations.status == "current"
    assert report.graph.status == "not_configured"


async def test_degraded_and_lists_pending_when_behind(
    sqlite_engine: AsyncEngine,
) -> None:
    await _stamp(sqlite_engine, "0002")
    report = await check_readiness(lambda: sqlite_engine, _no_graph())
    assert report.status == "degraded"
    assert report.migrations.status == "behind"
    assert report.migrations.current == "0002"
    assert report.migrations.pending[0] == "0003"
    assert report.migrations.pending[-1] == HEAD


async def test_never_migrated_database_lists_every_revision(
    sqlite_engine: AsyncEngine,
) -> None:
    report = await check_readiness(lambda: sqlite_engine, _no_graph())
    assert report.migrations.status == "behind"
    assert report.migrations.current is None
    assert report.migrations.pending[0] == "0001"


async def test_unknown_revision_is_reported_not_guessed(
    sqlite_engine: AsyncEngine,
) -> None:
    await _stamp(sqlite_engine, "9999")
    report = await check_readiness(lambda: sqlite_engine, _no_graph())
    assert report.migrations.status == "ahead_or_unknown"


async def test_not_ready_when_database_unreachable() -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://u:p@127.0.0.1:1/none", connect_args={"timeout": 1}
    )
    try:
        report = await check_readiness(lambda: engine, _no_graph(), timeout=3)
    finally:
        await engine.dispose()
    assert report.status == "not_ready"
    assert report.database.status == "unavailable"
    assert report.migrations.status == "unknown"


async def test_not_configured_database() -> None:
    def broken() -> AsyncEngine:
        raise ConfigurationException("bad url")

    report = await check_readiness(broken, _no_graph())
    assert report.database.status == "not_configured"
    assert report.status == "not_ready"


def test_ready_endpoint_is_public_and_reports_status_words(
    sqlite_engine: AsyncEngine,
) -> None:
    app = create_app()
    app.dependency_overrides[get_engine_factory] = lambda: lambda: sqlite_engine
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == {"status": "ok"}
    assert set(body) == {"status", "database", "migrations", "graph"}
    assert "postgresql" not in response.text and "bolt" not in response.text


def test_ready_endpoint_returns_503_when_not_ready() -> None:
    def broken() -> AsyncEngine:
        raise ConfigurationException("bad url")

    app = create_app()
    app.dependency_overrides[get_engine_factory] = lambda: broken
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
