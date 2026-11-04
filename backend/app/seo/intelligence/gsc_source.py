"""Read-only Search Console access for SEO Intelligence runs (monitor and insights).

Uses AURORA's stored Search Console credential (PostgreSQL + the token vault),
so the database container must be running. Every call is a read: Search
Analytics rows and URL Inspection results. Nothing here submits, indexes or
changes anything in Search Console.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from app.core.config import Settings
from app.core.exceptions import PersistenceException

GscQuery = Callable[[date, date, tuple[str, ...]], Awaitable[list[dict[str, object]]]]
GscInspect = Callable[[str], Awaitable[dict[str, Any]]]

MAX_GSC_REQUESTS = 20
DB_ATTEMPTS = 2
DB_RETRY_SECONDS = 20


async def _with_db_retry(call: Callable[[], Awaitable[Any]]) -> Any:
    """Reads only, so one retry after a pause is safe (the database container can
    be slow to accept connections right after ``docker compose up``)."""
    for attempt in range(1, DB_ATTEMPTS + 1):
        try:
            return await call()
        except PersistenceException:
            if attempt == DB_ATTEMPTS:
                raise
            await asyncio.sleep(DB_RETRY_SECONDS)
    return None


async def _with_tool(settings: Settings, use: Callable[[Any], Awaitable[Any]]) -> Any:
    from app.database.repositories.gsc_repository import GscRepository
    from app.database.session import create_session_factory, dispose_engine
    from app.monitoring.errors import GscStorageUnavailable
    from app.monitoring.service import RefreshTokenSource
    from app.monitoring.vault import select_vault
    from app.tools.search_console_tool import SearchConsoleTool

    try:
        async with create_session_factory()() as session:
            repo = GscRepository(session)
            try:
                vault = select_vault(settings.gsc_token_storage)
            except GscStorageUnavailable:
                vault = None
            tokens = RefreshTokenSource(repo, vault, settings)
            tool = SearchConsoleTool(settings, refresh_token_provider=tokens.get)
            return await use(tool)
    finally:
        await dispose_engine()


def gsc_query(
    settings: Settings, property_id: str, max_requests: int = MAX_GSC_REQUESTS
) -> GscQuery:
    """Search Analytics rows for any dimensions, paged until Google returns fewer
    rows than requested or ``max_requests`` pages were read."""

    async def query(
        start: date, end: date, dimensions: tuple[str, ...]
    ) -> list[dict[str, object]]:
        async def use(tool: Any) -> list[dict[str, object]]:
            rows: list[dict[str, object]] = []
            limit = settings.gsc_max_rows_per_request
            for page in range(max_requests):
                batch = await tool.query_performance(
                    property_id,
                    start.isoformat(),
                    end.isoformat(),
                    dimensions=dimensions,
                    row_limit=limit,
                    start_row=page * limit,
                )
                rows += [r.model_dump() for r in batch]
                if len(batch) < limit:
                    break
            return rows

        return await _with_db_retry(lambda: _with_tool(settings, use))

    return query


def gsc_inspect(settings: Settings, property_id: str) -> GscInspect:
    """URL Inspection (read-only index status of one URL)."""

    async def inspect(url: str) -> dict[str, Any]:
        async def use(tool: Any) -> dict[str, Any]:
            return await tool.inspect_url(property_id, url)

        return await _with_db_retry(lambda: _with_tool(settings, use))

    return inspect
