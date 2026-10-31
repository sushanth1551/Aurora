"""Controlled follow-up research: validated queries run through Module 3.

The LLM never sees or supplies URLs. A validated query string (and optionally a
company/product name) is handed to ``WebsiteIntelligenceService.run``, which
applies the existing SSRF policy, robots handling, provider budgets and crawl
limits. Results land in a new research job that the context builder merges.
"""

from uuid import UUID

from app.core.exceptions import ResearchException
from app.intelligence.models import FollowUpResearchQuery
from app.research.website_intelligence_service import WebsiteIntelligenceService
from app.schemas.intelligence import IntelligenceRequest, IntelligenceStatus

#: Keep follow-ups small: they are gap-filling, not new full studies.
FOLLOWUP_MAX_WEBSITES = 3
FOLLOWUP_MAX_PAGES_PER_SITE = 8


class WebsiteFollowUpExecutor:
    """Adapter from ``FollowUpResearchQuery`` to the Module 3 pipeline."""

    def __init__(self, service: WebsiteIntelligenceService) -> None:
        self._service = service

    async def execute(self, query: FollowUpResearchQuery) -> UUID:
        request = IntelligenceRequest(
            query=query.query[:300],
            target_names=[query.target_name] if query.target_name else None,
            max_websites=FOLLOWUP_MAX_WEBSITES,
            max_pages_per_site=FOLLOWUP_MAX_PAGES_PER_SITE,
        )
        response = await self._service.run(request)
        if response.status is IntelligenceStatus.FAILED:
            raise ResearchException("Follow-up research failed.")
        return response.job_id
