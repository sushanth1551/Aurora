"""SQLAlchemy async engine and session factory.

Nothing connects at import time: the engine object is created lazily on first
use and only opens a connection when a query runs.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.exc import ArgumentError, InvalidRequestError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationException


def get_database_url(settings: Settings | None = None) -> str:
    """Return the configured database URL without opening a connection.

    The URL may contain a password; do not log it.
    """
    return (settings or get_settings()).database_url.get_secret_value()


def create_engine(url: str, connect_timeout: float = 10.0) -> AsyncEngine:
    """Build an async engine with explicit connection and pool timeouts."""
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(
            pool_size=5,
            max_overflow=5,
            pool_timeout=connect_timeout,
            connect_args={"timeout": connect_timeout, "command_timeout": 30},
        )
    try:
        return create_async_engine(url, **kwargs)  # type: ignore[arg-type]
    except (ArgumentError, InvalidRequestError, ImportError) as exc:
        raise ConfigurationException(
            "DATABASE_URL is invalid or does not use an async driver; "
            "expected postgresql+asyncpg://user:password@host:5432/dbname."
        ) from exc


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Process-wide engine built from settings (created on first use)."""
    settings = get_settings()
    return create_engine(
        get_database_url(settings), settings.database_connect_timeout_seconds
    )


def create_session_factory(
    engine: AsyncEngine | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine`` (default: the process-wide engine)."""
    return async_sessionmaker(engine or get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request."""
    async with create_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    """Close pooled connections if an engine was ever created."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_engine.cache_clear()
