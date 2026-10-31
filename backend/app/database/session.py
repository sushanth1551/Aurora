"""SQLAlchemy engine and session factory boundary.

No engine is created at import time and no connection is opened.
"""

from app.core.config import Settings, get_settings


def get_database_url(settings: Settings | None = None) -> str:
    """Return the configured database URL without opening a connection.

    The URL may contain a password; do not log it.
    """
    return (settings or get_settings()).database_url.get_secret_value()


def create_session_factory() -> None:
    """Create an async engine and session factory (explicit, not on import)."""
    # TODO: sqlalchemy.ext.asyncio.create_async_engine with pool limits and
    # timeouts, async_sessionmaker, and a FastAPI dependency; dispose on shutdown.
    raise NotImplementedError("Database session setup is not implemented.")
