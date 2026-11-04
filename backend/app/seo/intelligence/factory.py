"""Build the SEO Intelligence services from settings (no I/O at construction)."""

from __future__ import annotations

from app.core.config import Settings
from app.seo.intelligence.insights.store import InsightStore
from app.seo.intelligence.knowledge import SeoKnowledgeRepository
from app.seo.intelligence.service import SeoAgentService
from app.seo.skills.improvement import SkillImprovementService
from app.seo.skills.registry import SkillRegistry

#: Settings whose presence decides whether a registered skill's step can run.
CREDENTIAL_SETTINGS = (
    "serpapi_api_key",
    "firecrawl_api_key",
    "google_client_id",
    "google_refresh_token",
    "groq_api_key",
    "anthropic_api_key",
    "dataforseo_login",
)


def configured_settings(settings: Settings) -> set[str]:
    """Names (never values) of the credential settings that are set."""
    return {name for name in CREDENTIAL_SETTINGS if getattr(settings, name, None)}


def build_registry(settings: Settings) -> SkillRegistry:
    return SkillRegistry(
        registry_path=settings.seo_intelligence_root / "skills" / "skill-registry.json",
        skills_dir=settings.seo_skills_dir,
        configured_settings=configured_settings(settings),
    )


def build_seo_agent_service(settings: Settings) -> SeoAgentService:
    return SeoAgentService(
        repository=SeoKnowledgeRepository(settings.seo_intelligence_root),
        registry=build_registry(settings),
    )


def build_skill_improvement(settings: Settings) -> SkillImprovementService:
    return SkillImprovementService(
        build_registry(settings), settings.seo_intelligence_root
    )


def build_insight_store(settings: Settings) -> InsightStore:
    return InsightStore(
        SeoKnowledgeRepository(settings.seo_intelligence_root),
        settings.seo_intelligence_data_root,
    )
