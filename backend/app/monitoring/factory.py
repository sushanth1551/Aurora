"""Builds the Search Console service from settings and a session.

Shared by the request dependency and the scheduler so both construct it the same
way. Nothing here connects to Google or the database.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.database.repositories.gsc_repository import GscRepository
from app.monitoring.errors import GscStorageUnavailable
from app.monitoring.oauth import OAuthFlows
from app.monitoring.service import GscService, RefreshTokenSource
from app.monitoring.vault import select_vault
from app.tools.search_console_tool import SearchConsoleTool


def build_gsc_service(
    session: AsyncSession, settings: Settings, flows: OAuthFlows
) -> GscService:
    repo = GscRepository(session)
    try:
        vault = select_vault(settings.gsc_token_storage)
    except GscStorageUnavailable:
        vault = None  # misconfigured storage mode: behave as "no secure storage"
    tokens = RefreshTokenSource(repo, vault, settings)
    tool = SearchConsoleTool(settings, refresh_token_provider=tokens.get)
    return GscService(
        repo=repo, settings=settings, tool=tool, tokens=tokens, vault=vault, flows=flows
    )
